# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Extend Foundry cloud evaluation to support 22 built-in evaluators (up from 8), covering quality, agent, safety, RAG, tool, and NLP evaluator categories. Verified end-to-end with live Foundry cloud evaluation.
  - Quality: `CoherenceEvaluator`, `FluencyEvaluator`, `RelevanceEvaluator`
  - Agent: `IntentResolutionEvaluator`, `TaskCompletionEvaluator`, `TaskAdherenceEvaluator`
  - Similarity: `ResponseCompletenessEvaluator`
  - RAG: `GroundednessProEvaluator`, `RetrievalEvaluator`
  - Safety: `ViolenceEvaluator`, `SexualEvaluator`, `SelfHarmEvaluator`, `HateUnfairnessEvaluator`
  - Tool: `ToolSelectionEvaluator`, `ToolInputAccuracyEvaluator`, `ToolOutputUtilizationEvaluator`, `ToolCallSuccessEvaluator`
- Add dynamic `item_schema` building — automatically includes `tool_definitions` and `context` fields when the enabled evaluators require them.
- Add CI/CD integration models documentation: PR quality gate, scheduled regression, post-deployment validation, multi-environment promotion, Azure DevOps pipeline.
- Add gating best practices: threshold design, scenario-specific evaluator selection, comparison-based regression detection.
- Add supported evaluators reference table to CI/CD documentation.
- Improve error messages when evaluators return no score (e.g. safety evaluators in unsupported regions) — surface the service error and suggest `--verbose`.
- Fix NLP evaluator names in frozensets to match `_to_builtin_evaluator_name` conversion (`bleu_score`, `rouge_score`, `gleu_score`, `meteor_score` instead of `bleu`, `rouge`, `gleu`, `meteor`).
- Add default `initialization_parameters` for `RougeScoreEvaluator` (`rouge_type: rouge1`).
- Implement `agentops eval compare --runs <baseline>,<current>` for baseline comparison of evaluation runs.
  - Produces `comparison.json` (structured metric deltas, threshold flips, item-level changes) and `comparison.md` (human-readable report).
  - Exits with code `0` (no regressions), `2` (regressions detected), or `1` (error).
  - Supports run IDs by timestamped folder name, `latest` keyword, or absolute/relative paths.
- Add Pydantic models for comparison output: `ComparisonResult`, `MetricDelta`, `ThresholdDelta`, `ItemDelta`, `ComparisonSummary`.
- Add comparison service (`services/comparison.py`) with run discovery and structured diff logic.
- Update `investigate-regression` and `run-evals` Copilot skills to reference the new compare command.
- Add distributable Copilot skills under `.github/plugins/agentops/skills/` for GitHub-based installation (`agentops-run-evals`, `agentops-investigate-regression`, `agentops-observability-triage`).
- Fix cloud evaluation to use the Foundry Project Evals API (`api-version=2025-11-15-preview`) with `azure_ai_evaluator` testing criteria, replacing the OpenAI SDK-based path that was incompatible.
- Fix metric polarity in comparison: lower-is-better metrics (e.g. `avg_latency_seconds` with `<=` threshold) now correctly show "improved" when they decrease.
- Align `azure-ai-projects` version references across all files to `>=2.0.1`.

### Changed
- Migrate versioning from static `pyproject.toml` field to `setuptools-scm` — version is now derived automatically from git tags.
- Redesign release pipeline into three workflow files:
  - `_build.yml` — reusable build workflow (test + package via setuptools-scm)
  - `staging.yml` — `release/*` branch pushes publish to TestPyPI and verify install
  - `release.yml` — `v*` tag pushes publish to TestPyPI, then PyPI (with approval gate), then create GitHub Release
- Add CLI smoke test in staging/release verify step (`agentops --version`, `agentops --help`, `agentops init`).
- Fix secret reference from `PIPY_TOKEN` to `PYPI_TOKEN`; add `TEST_PYPI_TOKEN` for TestPyPI.
- Add consistent workflow index header across all CI/CD workflow files.

## [0.1.0] - 2026-__-__

### Added
- `DatasetFormat.context_field` — optional field to declare the JSONL column holding retrieved context documents; used by `GroundednessEvaluator` in both cloud and local evaluation modes.
- `TaskCompletionEvaluator` support in the Foundry backend: default `input_mapping` and cloud `data_mapping` for both cloud and local modes.
- `ToolCallAccuracyEvaluator` support in the Foundry backend: `_EVALUATORS_NEEDING_TOOL_CALLS` set, cloud `data_mapping` (maps `tool_calls` from `{{sample.tool_calls}}` and `tool_definitions` from `{{item.tool_definitions}}`), and local `input_mapping`.
- `agent_tools_baseline` bundle upgraded from `SimilarityEvaluator` placeholder to `TaskCompletionEvaluator` + `ToolCallAccuracyEvaluator` with matching thresholds.
- `smoke-agent-tools.jsonl` enriched with `tool_definitions` and `tool_calls` fields for all 5 rows.
- Unit tests covering `_cloud_evaluator_data_mapping` (context_field, task_completion, tool_call_accuracy) and `_default_foundry_input_mapping` (GroundednessEvaluator, TaskCompletionEvaluator, ToolCallAccuracyEvaluator).

### Fixed
- `GroundednessEvaluator` in cloud mode now maps `context` to `{{item.<context_field>}}` when `context_field` is set in the dataset format, instead of incorrectly using the `expected_field` column.
- `GroundednessEvaluator` in local mode now maps `context` to `$row.context` (the retrieved documents column) instead of `$expected` (the ground truth answer).
- `smoke-rag.yaml` dataset config now declares `context_field: context` to correctly wire the `context` JSONL column to groundedness evaluation.

### Changed
- Split `agentops init` dataset seeds into `.agentops/datasets/` for YAML definitions and `.agentops/data/` for JSONL rows, and updated docs/examples to use the new layout.
- Expanded `agentops init` run-config seeds to include scenario-specific examples: `.agentops/run-rag.yaml` and `.agentops/run-agent.yaml` in addition to the default `.agentops/run.yaml`.
- Removed the runtime fallback to `gpt-5-mini` in the Foundry backend; model-direct mode now requires an explicit deployment via `backend.model` or `AZURE_AI_MODEL_DEPLOYMENT_NAME`.
- Added planned CLI command stubs with friendly "not implemented in this release" messages, and documented command availability/status in README and architecture docs.
- Reworked `README.md` into a walkthrough-oriented structure with a clearer overview, step-by-step onboarding flow, command status table, and documentation map.
- Refined `README.md` messaging to position AgentOps as a broader operations foundation (evaluation + planned CI/CD, tracing, observability, and monitoring capabilities), and renamed the onboarding section to `Quickstart`.

### Fixed
- Align README quickstart workspace tree and starter bundle table with current `agentops init` templates (`model_direct_baseline`, `rag_retrieval_baseline`, `agent_tools_baseline`, and smoke datasets).

### Added
- CLI command surface with Typer stubs:
  - `agentops init`
  - `agentops eval run --config <run.yaml> [--output <dir>]`
  - `agentops report --in <results.json> [--out <report.md>]`
- Unit tests for models, YAML/config loading, and workspace initialization behavior.
- Initial documentation including generic quickstart and test running guide.