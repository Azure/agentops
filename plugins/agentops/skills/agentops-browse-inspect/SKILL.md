---
name: agentops-browse-inspect
description: Browse evaluation bundles, inspect past runs, and explore evaluation history in an AgentOps workspace. Trigger when users ask to list bundles, show bundle details, list past runs, show run results, view run entries, inspect evaluation history, or check what evaluators are configured. Common phrases include "list bundles", "show bundle", "what bundles", "list runs", "show run", "view run", "run history", "past evaluations", "inspect run", "what evaluators", "browse evaluations", "check thresholds". Install agentops-toolkit via pip. Commands are agentops bundle list, agentops bundle show, agentops run list, agentops run show, and agentops run view.
---

# AgentOps Browse and Inspect

> **Prerequisite:** Install the AgentOps CLI with `pip install agentops-toolkit`.
> An initialized workspace (`.agentops/`) is required. Run `agentops init` if needed.

## Purpose

Browse evaluation bundles and inspect past evaluation runs in an AgentOps workspace. Useful for exploring available evaluators, reviewing run history, and understanding evaluation configurations.

## When to Use

- User asks what bundles or evaluators are available.
- User wants to see details of a specific bundle (evaluators, thresholds).
- User asks about past evaluation runs or run history.
- User wants to inspect results of a specific run.
- User asks which runs passed or failed thresholds.
- User wants to find the Foundry portal link for a run.

## Available Commands

```bash
agentops bundle list [--dir <dir>]                    # List evaluation bundles
agentops bundle show <name> [--dir <dir>]             # Show bundle details
agentops run list [--dir <dir>]                       # List past evaluation runs
agentops run show <run_id> [--dir <dir>]              # Show run summary
agentops run view <run_id> [--entry N]                # Deep-inspect run (planned)
```

### Key Flags

| Command | Flag | Description |
|---|---|---|
| `bundle list` | `--dir` | Workspace directory (default: current directory) |
| `bundle show` | `<name>` | Bundle name or filename without `.yaml` |
| `run list` | `--dir` | Workspace directory (default: current directory) |
| `run show` | `<run_id>` | Run ID (timestamp folder name or `latest`) |
| `run view` | `--entry N` | Row/entry index for deep inspection (planned) |

## Recommended Workflow

### Explore Available Bundles

List all bundles in the workspace:

```bash
agentops bundle list
```

Output shows each bundle's name, description, enabled evaluators, and threshold count:

```
Bundles in .agentops/bundles:

  model_direct_baseline
    Baseline evaluation for model-direct targets
    evaluators: SimilarityEvaluator, avg_latency_seconds
    thresholds: 2

  rag_retrieval_baseline
    Baseline evaluation for RAG retrieval
    evaluators: GroundednessEvaluator, SimilarityEvaluator, avg_latency_seconds
    thresholds: 3
```

### Inspect a Bundle

View full details of a specific bundle including evaluator settings and threshold definitions:

```bash
agentops bundle show model_direct_baseline
```

Output:

```
Bundle: model_direct_baseline
Path: .agentops/bundles/model_direct_baseline.yaml

Evaluators:
  SimilarityEvaluator (source=foundry, enabled)
  avg_latency_seconds (source=local, enabled)

Thresholds:
  SimilarityEvaluator >= 0.7
  avg_latency_seconds <= 5.0
```

### Browse Run History

List past evaluation runs sorted by most recent first:

```bash
agentops run list
```

Output:

```
Runs in .agentops/results:

  20250610-143022  PASS  bundle=model_direct_baseline  dataset=smoke-model-direct  duration=42.3s
  20250609-091500  FAIL  bundle=rag_retrieval_baseline  dataset=smoke-rag          duration=58.1s
```

### Inspect a Specific Run

Show the full summary of a run by its ID or use `latest`:

```bash
agentops run show latest
agentops run show 20250610-143022
```

Output includes:
- Run status (PASS/FAIL)
- Bundle and dataset used
- Backend type
- Start time and duration
- Items passed/failed counts
- Metric scores
- Threshold results with actual vs expected values
- Foundry portal URL (if cloud evaluation was used)

### Deep-Inspect a Run Entry (Planned)

The `run view` command will allow inspecting individual evaluation entries:

```bash
agentops run view 20250610-143022 --entry 3
```

This command is planned for a future release.

## Common Patterns

### Check if a bundle meets your needs

```bash
agentops bundle show rag_retrieval_baseline
```

Review the evaluators list to confirm the right metrics are being measured, then check thresholds to ensure quality gates match your requirements.

### Find which runs failed and why

```bash
agentops run list                          # Find runs with FAIL status
agentops run show <run_id>                 # Check threshold results
```

Look at the Thresholds section in the run output — it shows which specific evaluators failed with actual vs expected values.

### Compare with latest run

```bash
agentops run show latest                   # Current baseline
agentops eval compare --runs latest,<old_run_id>   # Side-by-side (from agentops-run-evals skill)
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Command succeeded |
| `1` | Runtime or configuration error (e.g., workspace not found, bundle not found) |
