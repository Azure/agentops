# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.3] - 2026-03-24

### Added
- **Auto-registration of skills in coding agent instruction files** — `agentops skills install` now registers installed skills in the coding agent's instruction file so AI assistants discover them automatically. For Copilot: appends an idempotent marker-delimited block to `.github/copilot-instructions.md` with a skill discovery table. For Cursor: writes a managed `.cursor/rules/agentops.mdc` file with `alwaysApply: true`. Repeated runs update the block in place (no duplicates).
- **Cursor platform detection** — `detect_platforms()` now recognises `.cursor/rules/` directory or `.cursorrules` file as Cursor indicators. Cursor skills are installed to `.github/skills/` (shared with Copilot) and registered via `.cursor/rules/agentops.mdc`.
- **Underscore Copilot filename detection** — `detect_platforms()` now silently accepts `copilot_instructions.md` (underscore variant) as a valid Copilot signal alongside the standard `copilot-instructions.md`.
- **`agentops skills install` command** — Installs packaged coding agent skills into consumer projects. Supports GitHub Copilot (`.github/skills/`), Cursor (`.github/skills/`), and Claude Code (`.claude/commands/`). Auto-detects platforms; falls back to GitHub Copilot silently. Pass `--prompt` to ask before installing when no platform is detected. Pass `--platform` for explicit platform selection.
- Packaged skill templates under `src/agentops/templates/skills/` for distribution via `pip install`.
- Extend Foundry cloud evaluation to support 22 built-in evaluators (up from 8), covering quality, agent, safety, RAG, tool, and NLP evaluator categories.
- Add dynamic `item_schema` building — automatically includes `tool_definitions` and `context` fields when the enabled evaluators require them.
- Fix NLP evaluator names in frozensets to match `_to_builtin_evaluator_name` conversion (`bleu_score`, `rouge_score`, `gleu_score`, `meteor_score` instead of `bleu`, `rouge`, `gleu`, `meteor`).
- Add default `initialization_parameters` for `RougeScoreEvaluator` (`rouge_type: rouge1`).
- Add optional OTLP tracing for evaluation runs — set `AGENTOPS_OTLP_ENDPOINT` to emit OpenTelemetry spans.
  - Three-layer schema: CICD semconv (pipeline run/task), GenAI semconv (agent invocation), and `agentops.eval.*` (evaluator scores/thresholds).
  - Per-row item spans with evaluator child spans showing score, threshold, and pass/fail.
  - Zero overhead when `AGENTOPS_OTLP_ENDPOINT` is unset; graceful no-op when `opentelemetry-sdk` is not installed.
- Browse commands: `agentops bundle list`, `agentops bundle show`, `agentops run list`, `agentops run show` for workspace inspection.

### Changed
- **Skills optimized for weaker models** — Rewrote all 8 SKILL.md files to reduce cognitive load and token usage. Key changes: replaced prose paragraphs with numbered single-action steps and tables, removed boilerplate ("Before You Start", "When to Use", "Purpose" sections), inlined decision logic into steps (no disconnected decision trees), provided one copy-paste callable adapter template instead of multiple variants, consolidated rules into a single section per skill. Size reductions: `agentops-eval` 613→275 lines (−55%), `agentops-config` 229→170 (−26%), `agentops-report` −35%, `agentops-regression` −35%, `agentops-monitor` −53%, `agentops-trace` −55%, `agentops-workflow` −38%, `agentops-dataset` −11%.
- **Skills discovery improvements** — `agentops-eval` and `agentops-config` skills now auto-discover container app URLs (`az containerapp list`) and webapp URLs (`az webapp list`), detect auth patterns from codebase (Dapr, API key, Bearer), pre-warm Azure CLI tokens to prevent intermittent `AzureCliCredential.get_token failed` errors, and present all discovered values as a confirmation table instead of asking each one separately.
- **Report readability improvements** — `report.md` and HTML reports now include: evaluator descriptions ("What It Measures" column), human-readable metric names (CamelCase split, `_` → spaces), ✅/❌ visual indicators for pass/fail, merged threshold columns (`>= 0.80` instead of separate Criteria/Expected), clean number formatting (drop unnecessary decimal zeros), per-row score tables in Row Details, retrieved context display for RAG evaluations (truncated at 500 chars), "How Pass/Fail Is Determined" section, and one-sentence descriptions after each section heading.
- **`RowMetricsResult` model updated** — Added optional `context` field to `RowMetricsResult` for RAG evaluation context display. All three backends (Foundry, HTTP, local adapter) now populate this field from dataset rows.
- **README restructured** — Simplified Quickstart from 6 steps to 3. Moved evaluation scenarios, configuration model, and run config examples to new `docs/concepts.md` page with ASCII architecture diagram. Removed Project Structure and Copilot Skills sections from README (available in CONTRIBUTING.md and tutorial-copilot-skills.md respectively).

### Added
- `docs/concepts.md` — new conceptual overview page with ASCII evaluation flow diagram, core concept definitions (workspace, run config, bundle, dataset, evaluator, backend), evaluation scenarios table, and configuration model summary.

### Changed
- **CLI refactored to entity-verb pattern** — All CLI commands now follow a consistent `<entity> <verb>` structure:
  - `agentops report` → `agentops report generate`
  - `agentops config cicd` → `agentops workflow generate` (new `workflow` entity)
  - `agentops monitor dashboard` → `agentops monitor show`
  - `agentops monitor alert` → `agentops monitor configure`
- **Skills refactored into modular skills** — 8 single-responsibility skills with `agentops-` prefix: `/agentops-eval` (run evaluations), `/agentops-config` (infer scenario + generate run.yaml), `/agentops-dataset` (generate JSONL + YAML datasets), `/agentops-report` (interpret and regenerate reports), `/agentops-regression` (investigate score drops), `/agentops-trace` (tracing stub), `/agentops-monitor` (monitoring stub), `/agentops-workflow` (CI/CD setup). Decomposed the monolithic `evals` skill into 4 focused skills. Each follows a standardized structure: Purpose, When to Use, Before You Start, Steps, Guardrails, Outputs.
- **Run config model** — The configuration model uses an orthogonal `target`/`hosting`/`execution_mode` model. Configs missing a `version` field or containing a legacy `backend` key are rejected with an actionable error message.
  - `target` section with `type` (agent|model), `hosting` (local|foundry|aks|containerapps), `execution_mode` (local|remote).
  - Remote endpoints configured via `target.endpoint` with `kind: foundry_agent` or `kind: http`.
  - Local adapter configured via `target.local.adapter`.
  - Bundle and dataset references support both `name` (convention-based) and `path` (explicit).
  - `execution` section with `concurrency` and `timeout_seconds`.
  - `run` section for optional `name` and `description` metadata.
- **Backend resolution** based on `execution_mode` + `endpoint.kind`.
- `BackendRunContext` carries full `RunConfig`.
- `publish_foundry_evaluation()` takes `endpoint_config: TargetEndpointConfig`.

### Added
- **Callable adapter mode** for `LocalAdapterBackend` — users can now specify a Python function (`module:function`) via `target.local.callable` instead of spawning a subprocess. The function receives `(input_text: str, context: dict) -> dict` and must return `{"response": "..."}`.
- **Shared evaluation engine** (`backends/eval_engine.py`) — evaluator loading, instantiation, execution, scoring, and dataset utilities extracted from `foundry_backend.py` into a standalone module shared by all backends.
- Starter templates: `callable_adapter.py` (example callable function) and `run-callable.yaml` (run config using callable mode), created by `agentops init`.
- Starter conversational dataset: `smoke-conversational.yaml` + `smoke-conversational.jsonl`, created by `agentops init`.
- Tutorials: `tutorial-conversational-agent.md` (Agent Framework conversational) and `tutorial-agent-workflow.md` (Agent Framework workflow with tools).
- `LocalAdapterConfig` now accepts `adapter` (subprocess) XOR `callable` (module:function) — both backward-compatible and validated.
- **Local adapter backend** (`local_adapter_backend.py`) — uses a stdin/stdout JSON protocol per dataset row.
- `TargetEndpointConfig`, `LocalAdapterConfig`, `TargetConfig`, `BundleRef`, `DatasetRef`, `ExecutionConfig`, `RunMetadata`, `OutputConfig` Pydantic models.
- Bundle/dataset name-based resolution: `resolve_bundle_ref()` and `resolve_dataset_ref()` in `config_loader.py`.
- Config validation with actionable error messages for missing `version` or legacy `backend` key.
- `tests/fixtures/fake_adapter.py` — stdin/stdout JSON echo adapter for integration tests.

### Removed
- `SubprocessBackend` (replaced by `LocalAdapterBackend`).
- `agent_http_baseline` bundle (replaced by scenario-specific bundles with HTTP runs).

### Changed
- **Evaluation bundles refactored** — renamed to outcome-focused names and added explicit evaluator configs:
  - `model_direct_baseline` → `model_quality_baseline` — with explicit `config` (kind, class_name, input_mapping, score_keys) for all evaluators.
  - `rag_retrieval_baseline` → `rag_quality_baseline` — with explicit evaluator config.
  - `agent_tools_baseline` → `agent_workflow_baseline` — with explicit evaluator config.
- All run templates updated to reference new bundle names.

### Added
- `conversational_agent_baseline` bundle — CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator, SimilarityEvaluator for chatbots and Q&A agents.
- `safe_agent_baseline` bundle — ViolenceEvaluator, SexualEvaluator, SelfHarmEvaluator, HateUnfairnessEvaluator, ProtectedMaterialEvaluator for content safety and responsible AI. Uses `azure_ai_project` (auto-injected from `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`).
- Safety evaluator backend support — auto-injects `azure_ai_project` for safety evaluator classes, cloud evaluation data mapping, and default input mappings.
- `docs/bundles.md` — comprehensive bundle documentation with per-bundle sections, input mapping variables, and threshold reference.

### Added
- **HTTP backend** (`type: http`) — new evaluation backend for agents deployed outside Microsoft Foundry Agent Service, such as LangGraph, LangChain, OpenAI SaaS, Microsoft Agent Framework applications on Azure Container Apps (ACA), or any custom REST endpoint.
  - Calls the agent endpoint row by row via HTTP POST.
  - Configurable via `url` (inline) or `url_env` (env var, recommended for CI).
  - Supports `request_field` (prompt key, default `message`), `response_field` (response key with dot-path support, default `text`), `auth_header_env` (Bearer token), and `headers` (static headers).
  - Supports `tool_calls_field` to extract tool call data from HTTP responses for agent-with-tools evaluators.
  - Supports `extra_fields` to forward additional JSONL row fields (e.g., `session_id`) in the request body.
  - Runs local evaluators (`exact_match`, `latency_seconds`, `avg_latency_seconds`) and AI-assisted foundry evaluators (via `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_MODEL_DEPLOYMENT_NAME`).
  - All three scenarios (model-direct, RAG, agent-with-tools) supported via HTTP.
  - No Foundry Agent Service dependency — works for multi-agent scenarios where the orchestrator exposes an HTTP endpoint.
- Add `TargetEndpointConfig` fields for HTTP: `url`, `url_env`, `request_field`, `response_field`, `auth_header_env`, `headers`, `tool_calls_field`, `extra_fields`.
- **Enriched evaluation bundles** with comprehensive predefined evaluators:
  - `model_quality_baseline` — `SimilarityEvaluator`, `CoherenceEvaluator`, `FluencyEvaluator`, `F1ScoreEvaluator`.
  - `rag_quality_baseline` — `GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator`, `CoherenceEvaluator`.
  - `agent_workflow_baseline` — `TaskCompletionEvaluator`, `ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, `ToolSelectionEvaluator`, `ToolInputAccuracyEvaluator`.
- Expanded cloud evaluator mappings: `_EVALUATORS_NEEDING_CONTEXT` now includes `relevance` and `retrieval`; `_EVALUATORS_NEEDING_TOOL_CALLS` now includes `tool_selection`, `tool_input_accuracy`, `tool_output_utilization`, `tool_call_success`.
- Added default input mappings for all new evaluators in `_default_foundry_input_mapping()`.
- `agentops init` now scaffolds HTTP scenario starter files:
  - `run-http-model.yaml` — HTTP model-direct run config.
  - `run-http-rag.yaml` — HTTP RAG run config.
  - `run-http-agent-tools.yaml` — HTTP agent-with-tools run config (with `tool_calls_field`).
  - `bundles/agent_http_baseline.yaml` removed (replaced by scenario-specific bundles).
- Add `docs/tutorial-http-agent.md` — end-to-end tutorial for the Agent Framework / ACA scenario.
- Add unit tests for `HttpBackend` (`tests/unit/test_http_backend.py`): URL resolution, request field, dot-path response extraction, latency metrics, auth header, `backend_metrics.json` schema.

- Implement `agentops eval compare --runs <baseline>,<current>` for baseline comparison of evaluation runs.
  - Produces `comparison.json` (structured metric deltas, threshold flips, item-level changes) and `comparison.md` (human-readable report).
  - Exits with code `0` (no regressions), `2` (regressions detected), or `1` (error).
  - Supports run IDs by timestamped folder name, `latest` keyword, or absolute/relative paths.
- Add Pydantic models for comparison output: `ComparisonResult`, `MetricDelta`, `ThresholdDelta`, `ItemDelta`, `ComparisonSummary`.
- Add comparison service (`services/comparison.py`) with run discovery and structured diff logic.
- Update `agentops-regression` and `agentops-eval` Copilot skills to reference the new compare command.
- Add distributable Copilot skills under `.github/plugins/agentops/skills/` for GitHub-based installation (`agentops-eval`, `agentops-config`, `agentops-dataset`, `agentops-report`, `agentops-regression`, `agentops-trace`, `agentops-monitor`, `agentops-workflow`).
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
- Add VSIX extension packaging and publishing to CI/CD pipeline; include Copilot skills in the VS Code Marketplace extension.

### Fixed
- Resolve all 37 mypy type errors across 6 source files (`foundry_backend.py`, `config_loader.py`, `reporter.py`, `browse.py`, `comparison.py`, `runner.py`).

## [0.1.0] - 2026-__-__

### Added
- `DatasetFormat.context_field` — optional field to declare the JSONL column holding retrieved context documents; used by `GroundednessEvaluator` in both cloud and local evaluation modes.
- `TaskCompletionEvaluator` support in the Foundry backend: default `input_mapping` and cloud `data_mapping` for both cloud and local modes.
- `ToolCallAccuracyEvaluator` support in the Foundry backend: `_EVALUATORS_NEEDING_TOOL_CALLS` set, cloud `data_mapping` (maps `tool_calls` from `{{sample.tool_calls}}` and `tool_definitions` from `{{item.tool_definitions}}`), and local `input_mapping`.
- `agent_workflow_baseline` bundle upgraded from `SimilarityEvaluator` placeholder to `TaskCompletionEvaluator` + `ToolCallAccuracyEvaluator` with matching thresholds.
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
- Align README quickstart workspace tree and starter bundle table with current `agentops init` templates (`model_quality_baseline`, `rag_quality_baseline`, `conversational_agent_baseline`, `agent_workflow_baseline`, and smoke datasets).

### Added
- CLI command surface with Typer stubs:
  - `agentops init`
  - `agentops eval run --config <run.yaml> [--output <dir>]`
  - `agentops report --in <results.json> [--out <report.md>]`
- Unit tests for models, YAML/config loading, and workspace initialization behavior.
- Initial documentation including generic quickstart and test running guide.