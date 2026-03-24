# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **HTTP backend** (`type: http`) — new evaluation backend for agents deployed outside Microsoft Foundry Agent Service, such as Microsoft Agent Framework applications on Azure Container Apps (ACA) or any custom REST endpoint.
  - Calls the agent endpoint row by row via HTTP POST.
  - Configurable via `url` (inline) or `url_env` (env var, recommended for CI).
  - Supports `request_field` (prompt key, default `message`), `response_field` (response key with dot-path support, default `text`), `auth_header_env` (Bearer token), and `headers` (static headers).
  - Runs local evaluators (`exact_match`, `latency_seconds`, `avg_latency_seconds`) and AI-assisted foundry evaluators (via `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_MODEL_DEPLOYMENT_NAME`).
  - No Foundry Agent Service dependency — works for multi-agent scenarios where the orchestrator exposes an HTTP endpoint.
- Add `BackendConfig` fields: `url`, `url_env`, `request_field`, `response_field`, `auth_header_env`, `headers`.
- `agentops init` now scaffolds HTTP scenario starter files:
  - `bundles/agent_http_baseline.yaml` — `IntentResolutionEvaluator`, `TaskCompletionEvaluator`, `CoherenceEvaluator`, `avg_latency_seconds` (reference-free); `ToolCallAccuracyEvaluator` included but disabled by default — enable when dataset rows carry `tool_calls` + `tool_definitions`.
  - `run-http.yaml` — run config wired to `url_env: AGENT_HTTP_URL`.
  - `datasets/smoke-http.yaml` + `data/smoke-http.jsonl` — generic Q&A smoke dataset.
- Add `docs/tutorial-http-agent.md` — end-to-end tutorial for the Agent Framework / ACA scenario.
- Add unit tests for `HttpBackend` (`tests/unit/test_http_backend.py`): URL resolution, request field, dot-path response extraction, latency metrics, auth header, `backend_metrics.json` schema.

### Added
- Implement `agentops eval compare --runs <baseline>,<current>` for baseline comparison of evaluation runs.
  - Produces `comparison.json` (structured metric deltas, threshold flips, item-level changes) and `comparison.md` (human-readable report).
  - Exits with code `0` (no regressions), `2` (regressions detected), or `1` (error).
  - Supports run IDs by timestamped folder name, `latest` keyword, or absolute/relative paths.
- Add Pydantic models for comparison output: `ComparisonResult`, `MetricDelta`, `ThresholdDelta`, `ItemDelta`, `ComparisonSummary`.
- Add comparison service (`services/comparison.py`) with run discovery and structured diff logic.
- Update `investigate-regression` and `run-evals` Copilot skills to reference the new compare command.
- Add distributable Copilot skills under `skills/` for GitHub-based installation (`agentops-run-evals`, `agentops-investigate-regression`, `agentops-observability-triage`).
- Add `plugin.json` at repo root — enables one-click **Chat: Install Plugin From Source** install flow in VS Code (agent plugins preview).
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