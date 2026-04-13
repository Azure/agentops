---
name: agentops-dataset-management
description: Guide users through creating, validating, and managing evaluation datasets for AgentOps. Trigger when users ask about dataset format, creating datasets, JSONL rows, dataset YAML config, dataset fields, validating datasets, describing datasets, importing datasets, input/expected/context fields, or dataset schema mapping. Common phrases include "create dataset", "validate dataset", "dataset format", "JSONL format", "dataset schema", "import dataset", "dataset fields", "input field", "expected field", "context field", "describe dataset", "dataset rows", "dataset YAML", "add evaluation data". Install agentops-toolkit via pip. Commands are agentops dataset validate, agentops dataset describe, and agentops dataset import.
---

# AgentOps Dataset Management

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.
> An initialized workspace (`.agentops/`) is required. Run `agentops init` if needed.

## Purpose

Guide users through creating, formatting, and managing evaluation datasets used by AgentOps evaluations. Covers the two-file dataset structure (YAML config + JSONL rows), field mapping for different evaluation scenarios, and dataset management commands.

## When to Use

- User wants to create a new evaluation dataset.
- User asks about dataset format or JSONL structure.
- User needs to understand field mapping (input, expected, context).
- User wants to validate a dataset before running an evaluation.
- User asks how to import data into AgentOps format.
- User wants to understand what fields different evaluators require.

## Available Commands

```bash
agentops dataset validate <path>                      # Validate dataset config (planned)
agentops dataset describe <path>                      # Describe dataset structure (planned)
agentops dataset import <source>                      # Import external data (planned)
```

> These commands are planned for a future release. This skill guides you through manual dataset creation and formatting.

## Dataset Structure

AgentOps uses a **two-file structure** for datasets:

1. **Dataset YAML config** — metadata, schema mapping, and path to JSONL rows
2. **Dataset JSONL file** — one JSON object per line containing evaluation data

### File Layout

```
.agentops/
├── datasets/
│   ├── smoke-model-direct.yaml     # Dataset config
│   ├── smoke-rag.yaml
│   └── smoke-agent-tools.yaml
└── data/
    ├── smoke-model-direct.jsonl    # Dataset rows
    ├── smoke-rag.jsonl
    └── smoke-agent-tools.jsonl
```

## Dataset YAML Config

The dataset YAML config defines metadata, the source JSONL path, and field mapping.

### Model-Direct Dataset

```yaml
version: 1
name: smoke-model-direct
description: Smoke test for model-direct evaluation
source:
  type: file
  path: ../data/smoke-model-direct.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
```

### RAG Dataset

```yaml
version: 1
name: smoke-rag
description: Smoke test for RAG evaluation
source:
  type: file
  path: ../data/smoke-rag.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
  context_field: context
```

### Agent with Tools Dataset

```yaml
version: 1
name: smoke-agent-tools
description: Smoke test for agent with tools evaluation
source:
  type: file
  path: ../data/smoke-agent-tools.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
```

### Key Fields

| Field | Required | Description |
|---|---|---|
| `version` | Yes | Schema version (currently `1`) |
| `name` | Yes | Dataset identifier |
| `description` | No | Human-readable description |
| `source.type` | Yes | Source type (`file`) |
| `source.path` | Yes | Relative path to JSONL file (relative to dataset YAML location) |
| `format.type` | Yes | Row format (`jsonl`) |
| `format.input_field` | Yes | Field name for evaluation input/query |
| `format.expected_field` | No | Field name for expected/ground truth answer |
| `format.context_field` | No | Field name for retrieval context (RAG scenarios) |

## JSONL Row Format

Each line in the JSONL file is a JSON object representing one evaluation item.

### Model-Direct Rows

```jsonl
{"input": "What is the capital of France?", "expected": "Paris"}
{"input": "Explain photosynthesis briefly.", "expected": "Photosynthesis converts sunlight into chemical energy in plants."}
```

### RAG Rows

```jsonl
{"input": "What are the return policy terms?", "expected": "30-day return window with receipt.", "context": "Our return policy allows returns within 30 days of purchase with a valid receipt."}
{"input": "What is the shipping time?", "expected": "3-5 business days.", "context": "Standard shipping takes 3-5 business days for domestic orders."}
```

### Agent with Tools Rows

```jsonl
{"input": "Book a meeting for tomorrow at 2pm", "expected": "Meeting booked for tomorrow at 2:00 PM"}
{"input": "What is the weather in Seattle?", "expected": "Current weather conditions in Seattle"}
```

## Creating a New Dataset

### Step 1: Create the JSONL Data File

Create a new file in `.agentops/data/`:

```bash
# Example: create a custom evaluation dataset
```

Write one JSON object per line. Each object must include at minimum the field specified by `input_field`:

```jsonl
{"input": "Your test query", "expected": "Expected response"}
```

### Step 2: Create the Dataset YAML Config

Create a new file in `.agentops/datasets/`:

```yaml
version: 1
name: my-custom-dataset
description: Custom evaluation dataset for my agent
source:
  type: file
  path: ../data/my-custom-dataset.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
```

### Step 3: Reference in run.yaml

Update your run configuration to use the new dataset:

```yaml
dataset:
  path: datasets/my-custom-dataset.yaml
```

## Field Requirements by Evaluator Type

Different evaluators require different fields in the dataset:

| Evaluator Category | Required Fields | Optional Fields |
|---|---|---|
| Similarity (SimilarityEvaluator) | `input`, `expected` | — |
| Groundedness (GroundednessEvaluator) | `input`, `context` | `expected` |
| RAG evaluators (RelevanceEvaluator, etc.) | `input`, `context` | `expected` |
| Tool evaluators (ToolCallAccuracyEvaluator) | `input` | `expected`, `tool_definitions` |
| Task completion (TaskCompletionEvaluator) | `input`, `expected` | — |
| Latency (avg_latency_seconds) | `input` | — |

## Validation Checklist

Before running an evaluation, verify:

1. **JSONL format** — Each line is valid JSON, no trailing commas.
2. **Required fields** — Every row has the `input_field` defined in the YAML config.
3. **Expected fields** — Rows include `expected` if the bundle uses similarity or task-completion evaluators.
4. **Context fields** — Rows include `context` if the bundle uses groundedness or RAG evaluators.
5. **Path reference** — The `source.path` in dataset YAML correctly points to the JSONL file.
6. **Encoding** — Files are UTF-8 encoded.

## Troubleshooting

- **"Dataset file not found"** — Check that `source.path` in the YAML config is correct relative to the dataset YAML file location.
- **"Missing required field"** — Ensure every JSONL row contains the field specified by `format.input_field`.
- **"Invalid JSON"** — Check JSONL file for syntax errors. Each line must be valid JSON.
- **Evaluator returns null scores** — The dataset may be missing fields that the evaluator requires (e.g., `context` for groundedness).
