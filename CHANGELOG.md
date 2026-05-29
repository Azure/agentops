# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **Cloud-eval parser no longer returns null scores for Foundry
  `azure_ai_evaluator` graders.** The parser now probes a wider set of
  score-carrier keys (`score`, `value`, `result`, `metric_value`,
  `rating`, `grader_score`, `numeric_value`), falls back to `passed`
  (bool) and then `label` (`"pass"` / `"fail"` strings), and descends
  into `sample` / `details` as a final resort. Treats `score: 0` as a
  legitimate value (was previously coerced to `None` in some paths).
  Without this fix, every metric in a Foundry cloud run came back
  `value: null` against the real on-the-wire shape — the `report.md`
  threshold table showed every metric as `actual=missing` and exit code
  2 fired with `Threshold status: FAILED` even when the run itself
  succeeded.

### Added
- **`cloud_output_items.json` raw dump.** Every cloud eval run now
  writes the raw `output_items` it received from Foundry to
  `<output_dir>/cloud_output_items.json`, in addition to the parsed
  `results.json`. When a future grader / SDK upgrade changes the on-the-
  wire shape and the parser stops finding scores, the artifact bundle
  alone is enough to triage the issue. The orchestrator also emits an
  explicit warning to the progress channel when a cloud run yields zero
  usable metric scores despite returning rows, pointing the user at the
  new dump file.

## [0.3.0] - 2026-05-28

### Added
- **`--doctor-gate` flag on `agentops workflow generate`.** New option
  `--doctor-gate critical|warning|none` controls the Doctor severity floor
  in the PR workflow template. Default is `critical`, which makes the PR
  Doctor step block on critical Doctor findings (notably the
  `regression.<metric>` checks that fire when an evaluator metric drops
  meaningfully from the rolling baseline). This catches drift such as
  groundedness moving from 5.0 to 4.0 even when the configured eval
  thresholds technically still pass. `--doctor-gate warning` blocks on
  warnings or higher; `--doctor-gate none` restores the pre-1.x advisory
  behavior. Only the PR template is affected — deploy templates continue
  to run with `--severity-fail critical` regardless.
- **Stage-then-eval PR workflow for Foundry prompt agents.** When
  `--deploy-mode prompt-agent` is in effect, `agentops workflow generate
  --kinds pr` now emits a PR workflow (and Azure DevOps pipeline) that
  stages an ephemeral Foundry candidate prompt-agent version from
  `prompt_file` in the dev Foundry project, then evaluates that exact
  candidate (instead of the seed agent pinned in `agentops.yaml`). This
  makes the PR gate meaningful for prompt-agent flows: regressions are
  caught at PR time, not after merge. Candidates accumulate in the dev
  project across PRs and may need periodic cleanup.

### Changed
- **Renamed PyPI distribution and VS Code publisher.** The PyPI
  distribution name changed from `agentops-toolkit` to
  `agentops-accelerator`, and the VS Code Marketplace publisher
  changed from `AgentOpsToolkit` to `AgentOpsAccelerator`. The
  resulting extension ID flips from
  `AgentOpsToolkit.agentops-toolkit` to
  `AgentOpsAccelerator.agentops-accelerator`. The Python import
  (`import agentops`) and CLI command (`agentops ...`) are
  unchanged — only the install/distribution identifier changed.
  Install with `pip install agentops-accelerator` or
  `uv pip install agentops-accelerator`. Two deprecation tombstones
  are published atomically with this release so existing users are
  guided to the new identifiers:

  - **PyPI tombstone**: `pip install agentops-toolkit` keeps working
    via a metapackage at `tombstones/pypi/` that pins
    `agentops-accelerator>=0.3.0` (no shadow code, no auto-discovery).
    The package long-description on PyPI links to the migration
    instructions.
  - **VS Code Marketplace tombstone**: a final
    `AgentOpsToolkit.agentops-toolkit` extension at `tombstones/vscode/`
    activates with a one-time prompt offering to install
    `AgentOpsAccelerator.agentops-accelerator` (or open the Marketplace
    page in a browser). A per-install storage sentinel prevents
    re-prompts after the user resolves it.

  The release tag (`v0.3.0`) drives all four publishes
  (agentops-accelerator + agentops-toolkit on PyPI, plus the new and
  legacy VSIX publishers) through gated jobs in `release.yml`. The
  tombstones are gated AFTER the corresponding main publish jobs so
  the worst-case failure mode is "tombstone delayed, recoverable in
  v0.3.1" — never "tombstone-without-new". (#181)
- **Default PR Doctor behavior is now blocking.** Generating workflows
  without `--doctor-gate` produces a PR template that blocks on critical
  Doctor findings. Existing workflows continue to work unchanged; only
  re-generated workflows pick up the new default. To opt back into the
  previous advisory behavior, run
  `agentops workflow generate --doctor-gate none --force`.
- **`--deploy-mode prompt-agent` now changes the generated PR workflow.**
  Re-running `agentops workflow generate --deploy-mode prompt-agent
  --kinds pr,dev,qa,prod --force` produces a different PR template than
  before (it now stages a Foundry candidate before evaluating). Other
  modes (`auto`, `placeholder`, `azd`) continue to produce the previous
  generic PR template.
- Prompt and hosted agent eval defaults now use judge-based response
  completeness instead of token-overlap F1, keeping F1 as the default for
  exact-reference `model:<deployment>` checks or explicit evaluator overrides.

### Notes for developers
- **Editable install cleanup after rebrand.** Developers with an
  existing local editable install (`uv pip install -e .` or
  `pip install -e .`) may have a stale
  `src/agentops_toolkit.egg-info/` directory or stale
  `importlib.metadata` entries pointing to the old distribution
  name after pulling this release. Clean up with:
  `rm -rf src/*.egg-info && uv pip install -e .` (or
  `rm -rf src/*.egg-info && pip install -e .` for pip). This is
  a one-time, dev-only step; CI runs are unaffected because they
  create fresh virtual environments, and end users installing
  from PyPI are unaffected because wheels carry the new
  `dist-info` directory directly. (#181)

## [0.2.2] - 2026-05-26

### Fixed
- **Release workflow verification.** Release builds now pin package versions from
  the release tag, assert the generated distribution matches that version, and
  fail TestPyPI verification immediately when the expected package is not
  available.

## [0.2.1] - 2026-05-26

### Changed
- Consolidated the tutorial set into two quickstarts plus one end-to-end
  Foundry + AgentOps workshop, with the quickstarts now covering the broader
  Foundry build/debug/evaluate/observe journey before AgentOps readiness.
- Made the quickstarts self-contained around a Travel Agent example, including
  prompt-agent creation, hosted HTTP endpoint creation, and travel-specific
  eval datasets without local workspace install paths.
- Updated the tutorials to prefer the interactive `agentops init` wizard,
  explain evaluator deployment separately from initialization, and include
  forced regression/fix loops for prompt and hosted agent paths.
- Re-ask starter `agent` and `dataset` values during the first interactive
  `agentops init` run so tutorial users replace `my-agent:1` with their target.
- Removed the interactive App Insights question from `agentops init`; runtime
  commands discover it from the Foundry project when possible, and
  `--appinsights-connection-string` remains available for explicit setup.
- Made `workflow analyze` output use a lighter PowerShell-friendly summary,
  Markdown tables, and user-facing Foundry eval labels; also removed a
  non-actionable latency warning from the normal analysis output.
- Made `workflow generate` next steps gentler for PowerShell and tutorial users:
  PR/watchdog-only output now asks for only the `dev` environment, explains
  that deploy setup can wait, and points users to Copilot-assisted GitHub/OIDC
  setup.

### Fixed
- **Doctor App Insights discovery.** The `azure_monitor` source now falls back
  to an App Insights `ApplicationId` from `APPLICATIONINSIGHTS_CONNECTION_STRING`
  or Foundry project telemetry discovery, so Doctor no longer reports runtime
  telemetry as unconfigured when Cockpit can already resolve App Insights.

## [0.2.0] - 2026-05-22

### Added
- **Release evidence packs.** Added the release evidence schema and Doctor
  evidence-pack writer so teams can produce review-ready production promotion
  artifacts from existing readiness signals.
- **Trace promotion workflow.** Added trace export promotion into reviewable
  dataset candidates so production learnings can become future regression
  coverage.
- **Workflow analysis and prompt-agent deployment templates.** Added CI/CD
  analysis plus GitHub Actions and Azure DevOps templates for prompt-agent
  deployment paths.
- **Production readiness guidance.** Added the production readiness tutorial
  and release-readiness Doctor checks to connect evaluation gates, evidence,
  and deployment readiness.
- **Pre-flight checks for `agentops eval run`** - detects common issues (missing `azure-identity` or `azure-ai-evaluation` packages, missing env vars for AI-assisted/safety evaluators, Azure credential failures, unreachable endpoints) *before* backend execution. All detectable issues are reported at once with actionable error messages and `pip install` hints.
- **`--dry-run` / `-n` flag on `eval run`** - runs pre-flight checks without executing the evaluation. Exits 0 if all checks pass, 1 otherwise. Useful for CI gating and fast feedback.
- **Credential warm-up in pre-flight** - acquires and caches the MSAL token once during pre-flight so subsequent evaluator calls don't each cold-start `az.cmd`.

### Changed
- **AgentOps 1.0 workspace and documentation refresh.** Updated the CLI,
  templates, skills, examples, and docs around the flat `agentops.yaml`
  workflow, azd-compatible initialization, Doctor/Cockpit readiness, and
  production evaluation loops.
- **`AZURE_OPENAI_ENDPOINT` is now auto-normalized.** When the env
  var includes the portal-style inference-path suffix
  (e.g. `https://<resource>.openai.azure.com/openai/v1`,
  `/openai/`, `/openai/deployments`), AgentOps strips it before
  passing the value to the `azure-ai-evaluation` SDK and the
  `openai` client. Trailing slashes are also trimmed. The user can
  paste whichever URL the Foundry portal showed and the eval
  pipeline now works transparently.
- **Doctor categories aligned to WAF-AI pillars (breaking).** The
  `genaiops` category was renamed to `operational_excellence` to match
  the Microsoft Well-Architected Framework for AI pillar names. Every
  doctor finding id with the `genaiops.` prefix was renamed to `opex.`
  (the WAF checklist, cockpit rows, report grouping, and CLI
  `--categories` flag are now all named consistently). The
  `checks/mlops.py` module was renamed to `checks/opex_workspace.py`
  and its function `run_mlops_check` to `run_opex_workspace_check`.
  A read-only legacy-id shim (`agentops/agent/_legacy_ids.py`)
  rewrites legacy `genaiops.*` rule ids in
  `checks.llm_assist.rules` and legacy `--categories genaiops` flags
  in memory at load time with a one-shot deprecation warning; update
  your config to the canonical names - the legacy aliases will be
  removed in a future release.
- **Azure CLI credential timeout raised to 30s** - all `DefaultAzureCredential` instantiation sites (`eval_engine.py`, `foundry_backend.py`) now pass `process_timeout=30`. Default (10s) is insufficient for Windows `az.cmd` cold starts and was causing intermittent `AzureCliCredential: Failed to invoke the Azure CLI` errors.

## [0.1.7] - 2026-04-21

### Added
- **Single source of truth for skills (closes #87)** - `src/agentops/templates/skills/` is now the canonical location. Added `scripts/sync-skills.sh` and `scripts/sync-skills.ps1` to propagate changes to `plugins/agentops/skills/`. CI test `test_skills_sync.py` fails if the two directories diverge.
- **Optional unit test generation** - `agentops-eval` skill (Step 1) now offers to generate unit tests for agent code when no existing tests are detected. Generates `pytest` + `unittest.mock` tests covering endpoint handlers, response parsing, and error handling. Opt-in only - skips silently if tests already exist or user declines.

### Changed
- **Cross-platform subprocess handling in generated scripts** - `agentops-eval` and `agentops-dataset` skills now instruct generated `rag_context.py` scripts to use `shutil.which()` + `shell=(sys.platform == "win32")` when calling external CLIs, preventing `FileNotFoundError` on Windows.
- **Auth detection carrythrough to callable adapter** - `agentops-eval` skill Step 5.5 now explicitly wires the auth pattern detected in Step 2 into the adapter using generic `AGENT_AUTH_HEADER` and `AGENT_AUTH_TOKEN` env vars. Updated `callable_adapter.py` template to use the same generic auth mechanism. Prevents 401 errors on first smoke test.
- **azd environment validation** - `agentops-eval` (Step 4) and `agentops-config` (Step 3) skills now validate azd environments before trusting `.azure/<env>/.env` values: checks `azd env list`, verifies resource group exists via `az group exists`, and warns on stale environments.
- **Enhanced smoke test diagnostics** - `agentops-eval` skill Step 6 smoke test now checks for empty responses, response length, response format mismatches (JSON vs SSE), unexpected prefixes (UUIDs), and HTML error pages. Expanded troubleshooting table with specific remediation steps.
- **Updated CONTRIBUTING.md** - added single-source-of-truth rule for skills and sync script instructions.

## [0.1.6] - 2026-04-15

### Changed
- **Unified changelog** - removed separate `plugins/agentops/CHANGELOG.md`; CI now copies the root changelog into the VSIX package. Single source of truth for both CLI and extension.
- **Removed `[Unreleased]` changelog pattern** - changelog entries are now added directly under versioned sections.
- **Configured Dependabot** - added `.github/dependabot.yml` targeting `develop` for pip, GitHub Actions, and npm ecosystems.

## [0.1.5] - 2026-04-13

### Fixed
- **Make release pipeline resilient to VSIX version conflicts** - add `continue-on-error` on VSIX publish and decouple GitHub Release from VSIX publish result, preventing staging pre-release "already exists" failures from blocking the release.
- **Resolve 31 mypy type errors and enforce mypy in CI** - strict type checking added to the `lint` job (`mypy --strict src/`), fixing errors across `foundry_backend.py`, `eval_engine.py`, `reporter.py`, `runner.py`, `comparison.py`, and `browse.py`.
- **Resolve 18 ruff lint errors** (F401 unused imports, F811 redefinition, F841 unused variables) across 6 source and test files.
- **Fix UV cache race condition in CI** - disable UV cache on non-matrix jobs (lint, coverage, publish-dev) that shared cache keys with the test matrix, eliminating `Failed to save: Unable to reserve cache` warnings.

### Changed
- **Upgrade GitHub Actions to Node.js 24 runtimes** - update `actions/checkout` to v6, `actions/setup-python` to v5, `astral-sh/setup-uv` to v7, `actions/upload-artifact` and `download-artifact` to v7 across all CI/CD workflows.
- **Apply ruff-format across source and workflows** - normalize code style and whitespace across backends, services, CLI, tests, and workflow YAML files.

## [0.1.4] - 2026-04-14

### Fixed
- Resolve all 37 mypy type errors across 6 source files (`foundry_backend.py`, `config_loader.py`, `reporter.py`, `browse.py`, `comparison.py`, `runner.py`).
- Fix VSIX version derivation in CI/CD workflows - use global tag sort (`git tag -l --sort=-v:refname`) instead of `git describe` which misses tags not reachable from the current branch.

## [0.1.3] - 2026-03-24

### Added
- **Auto-registration of skills in coding agent instruction files** - `agentops skills install` now registers installed skills in the coding agent's instruction file so AI assistants discover them automatically. For Copilot: appends an idempotent marker-delimited block to `.github/copilot-instructions.md` with a skill discovery table. For Cursor: writes a managed `.cursor/rules/agentops.mdc` file with `alwaysApply: true`. Repeated runs update the block in place (no duplicates).
- **Cursor platform detection** - `detect_platforms()` now recognises `.cursor/rules/` directory or `.cursorrules` file as Cursor indicators. Cursor skills are installed to `.github/skills/` (shared with Copilot) and registered via `.cursor/rules/agentops.mdc`.
- **Underscore Copilot filename detection** - `detect_platforms()` now silently accepts `copilot_instructions.md` (underscore variant) as a valid Copilot signal alongside the standard `copilot-instructions.md`.
- **`agentops skills install` command** - Installs packaged coding agent skills into consumer projects. Supports GitHub Copilot (`.github/skills/`), Cursor (`.github/skills/`), and Claude Code (`.claude/commands/`). Auto-detects platforms; falls back to GitHub Copilot silently. Pass `--prompt` to ask before installing when no platform is detected. Pass `--platform` for explicit platform selection.
- Packaged skill templates under `src/agentops/templates/skills/` for distribution via `pip install`.
- Extend Foundry cloud evaluation to support 22 built-in evaluators (up from 8), covering quality, agent, safety, RAG, tool, and NLP evaluator categories.
- Add dynamic `item_schema` building - automatically includes `tool_definitions` and `context` fields when the enabled evaluators require them.
- Fix NLP evaluator names in frozensets to match `_to_builtin_evaluator_name` conversion (`bleu_score`, `rouge_score`, `gleu_score`, `meteor_score` instead of `bleu`, `rouge`, `gleu`, `meteor`).
- Add default `initialization_parameters` for `RougeScoreEvaluator` (`rouge_type: rouge1`).
- Add optional OTLP tracing for evaluation runs - set `AGENTOPS_OTLP_ENDPOINT` to emit OpenTelemetry spans.
  - Three-layer schema: CICD semconv (pipeline run/task), GenAI semconv (agent invocation), and `agentops.eval.*` (evaluator scores/thresholds).
  - Per-row item spans with evaluator child spans showing score, threshold, and pass/fail.
  - Zero overhead when `AGENTOPS_OTLP_ENDPOINT` is unset; graceful no-op when `opentelemetry-sdk` is not installed.
- Browse commands: `agentops bundle list`, `agentops bundle show`, `agentops run list`, `agentops run show` for workspace inspection.

### Changed
- **Skills optimized for weaker models** - Rewrote all 8 SKILL.md files to reduce cognitive load and token usage. Key changes: replaced prose paragraphs with numbered single-action steps and tables, removed boilerplate ("Before You Start", "When to Use", "Purpose" sections), inlined decision logic into steps (no disconnected decision trees), provided one copy-paste callable adapter template instead of multiple variants, consolidated rules into a single section per skill. Size reductions: `agentops-eval` 613→275 lines (−55%), `agentops-config` 229→170 (−26%), `agentops-report` −35%, `agentops-regression` −35%, `agentops-monitor` −53%, `agentops-trace` −55%, `agentops-workflow` −38%, `agentops-dataset` −11%.
- **Skills discovery improvements** - `agentops-eval` and `agentops-config` skills now auto-discover container app URLs (`az containerapp list`) and webapp URLs (`az webapp list`), detect auth patterns from codebase (Dapr, API key, Bearer), pre-warm Azure CLI tokens to prevent intermittent `AzureCliCredential.get_token failed` errors, and present all discovered values as a confirmation table instead of asking each one separately.
- **Report readability improvements** - `report.md` and HTML reports now include: evaluator descriptions ("What It Measures" column), human-readable metric names (CamelCase split, `_` → spaces), ✅/❌ visual indicators for pass/fail, merged threshold columns (`>= 0.80` instead of separate Criteria/Expected), clean number formatting (drop unnecessary decimal zeros), per-row score tables in Row Details, retrieved context display for RAG evaluations (truncated at 500 chars), "How Pass/Fail Is Determined" section, and one-sentence descriptions after each section heading.
- **`RowMetricsResult` model updated** - Added optional `context` field to `RowMetricsResult` for RAG evaluation context display. All three backends (Foundry, HTTP, local adapter) now populate this field from dataset rows.
- **README restructured** - Simplified Quickstart from 6 steps to 3. Moved evaluation scenarios, configuration model, and run config examples to new `docs/concepts.md` page with ASCII architecture diagram. Removed Project Structure and Copilot Skills sections from README (available in CONTRIBUTING.md and tutorial-copilot-skills.md respectively).

### Added
- `docs/concepts.md` - new conceptual overview page with ASCII evaluation flow diagram, core concept definitions (workspace, run config, bundle, dataset, evaluator, backend), evaluation scenarios table, and configuration model summary.

### Changed
- **CLI refactored to entity-verb pattern** - All CLI commands now follow a consistent `<entity> <verb>` structure:
  - `agentops report` → `agentops report generate`
  - `agentops config cicd` → `agentops workflow generate` (new `workflow` entity)
  - `agentops monitor cockpit` → `agentops monitor show`
  - `agentops monitor alert` → `agentops monitor configure`
- **Skills refactored into modular skills** - 8 single-responsibility skills with `agentops-` prefix: `/agentops-eval` (run evaluations), `/agentops-config` (infer scenario + generate run.yaml), `/agentops-dataset` (generate JSONL + YAML datasets), `/agentops-report` (interpret and regenerate reports), `/agentops-regression` (investigate score drops), `/agentops-trace` (tracing stub), `/agentops-monitor` (monitoring stub), `/agentops-workflow` (CI/CD setup). Decomposed the monolithic `evals` skill into 4 focused skills. Each follows a standardized structure: Purpose, When to Use, Before You Start, Steps, Guardrails, Outputs.
- **Run config model** - The configuration model uses an orthogonal `target`/`hosting`/`execution_mode` model. Configs missing a `version` field or containing a legacy `backend` key are rejected with an actionable error message.
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
- **Callable adapter mode** for `LocalAdapterBackend` - users can now specify a Python function (`module:function`) via `target.local.callable` instead of spawning a subprocess. The function receives `(input_text: str, context: dict) -> dict` and must return `{"response": "..."}`.
- **Shared evaluation engine** (`backends/eval_engine.py`) - evaluator loading, instantiation, execution, scoring, and dataset utilities extracted from `foundry_backend.py` into a standalone module shared by all backends.
- Starter templates: `callable_adapter.py` (example callable function) and `run-callable.yaml` (run config using callable mode), created by `agentops init`.
- Starter conversational dataset: `smoke-conversational.yaml` + `smoke-conversational.jsonl`, created by `agentops init`.
- Tutorials: `tutorial-conversational-agent.md` (Agent Framework conversational) and `tutorial-agent-workflow.md` (Agent Framework workflow with tools).
- `LocalAdapterConfig` now accepts `adapter` (subprocess) XOR `callable` (module:function) - both backward-compatible and validated.
- **Local adapter backend** (`local_adapter_backend.py`) - uses a stdin/stdout JSON protocol per dataset row.
- `TargetEndpointConfig`, `LocalAdapterConfig`, `TargetConfig`, `BundleRef`, `DatasetRef`, `ExecutionConfig`, `RunMetadata`, `OutputConfig` Pydantic models.
- Bundle/dataset name-based resolution: `resolve_bundle_ref()` and `resolve_dataset_ref()` in `config_loader.py`.
- Config validation with actionable error messages for missing `version` or legacy `backend` key.
- `tests/fixtures/fake_adapter.py` - stdin/stdout JSON echo adapter for integration tests.

### Removed
- `SubprocessBackend` (replaced by `LocalAdapterBackend`).
- `agent_http_baseline` bundle (replaced by scenario-specific bundles with HTTP runs).

### Changed
- **Evaluation bundles refactored** - renamed to outcome-focused names and added explicit evaluator configs:
  - `model_direct_baseline` → `model_quality_baseline` - with explicit `config` (kind, class_name, input_mapping, score_keys) for all evaluators.
  - `rag_retrieval_baseline` → `rag_quality_baseline` - with explicit evaluator config.
  - `agent_tools_baseline` → `agent_workflow_baseline` - with explicit evaluator config.
- All run templates updated to reference new bundle names.

### Added
- `conversational_agent_baseline` bundle - CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator, SimilarityEvaluator for chatbots and Q&A agents.
- `safe_agent_baseline` bundle - ViolenceEvaluator, SexualEvaluator, SelfHarmEvaluator, HateUnfairnessEvaluator, ProtectedMaterialEvaluator for content safety and responsible AI. Uses `azure_ai_project` (auto-injected from `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`).
- Safety evaluator backend support - auto-injects `azure_ai_project` for safety evaluator classes, cloud evaluation data mapping, and default input mappings.
- `docs/bundles.md` - comprehensive bundle documentation with per-bundle sections, input mapping variables, and threshold reference.

### Added
- **HTTP backend** (`type: http`) - new evaluation backend for agents deployed outside Microsoft Foundry Agent Service, such as LangGraph, LangChain, OpenAI SaaS, Microsoft Agent Framework applications on Azure Container Apps (ACA), or any custom REST endpoint.
  - Calls the agent endpoint row by row via HTTP POST.
  - Configurable via `url` (inline) or `url_env` (env var, recommended for CI).
  - Supports `request_field` (prompt key, default `message`), `response_field` (response key with dot-path support, default `text`), `auth_header_env` (Bearer token), and `headers` (static headers).
  - Supports `tool_calls_field` to extract tool call data from HTTP responses for agent-with-tools evaluators.
  - Supports `extra_fields` to forward additional JSONL row fields (e.g., `session_id`) in the request body.
  - Runs local evaluators (`exact_match`, `latency_seconds`, `avg_latency_seconds`) and AI-assisted foundry evaluators (via `AZURE_OPENAI_ENDPOINT` / `AZURE_AI_MODEL_DEPLOYMENT_NAME`).
  - All three scenarios (model-direct, RAG, agent-with-tools) supported via HTTP.
  - No Foundry Agent Service dependency - works for multi-agent scenarios where the orchestrator exposes an HTTP endpoint.
- Add `TargetEndpointConfig` fields for HTTP: `url`, `url_env`, `request_field`, `response_field`, `auth_header_env`, `headers`, `tool_calls_field`, `extra_fields`.
- **Enriched evaluation bundles** with comprehensive predefined evaluators:
  - `model_quality_baseline` - `SimilarityEvaluator`, `CoherenceEvaluator`, `FluencyEvaluator`, `F1ScoreEvaluator`.
  - `rag_quality_baseline` - `GroundednessEvaluator`, `RelevanceEvaluator`, `RetrievalEvaluator`, `ResponseCompletenessEvaluator`, `CoherenceEvaluator`.
  - `agent_workflow_baseline` - `TaskCompletionEvaluator`, `ToolCallAccuracyEvaluator`, `IntentResolutionEvaluator`, `TaskAdherenceEvaluator`, `ToolSelectionEvaluator`, `ToolInputAccuracyEvaluator`.
- Expanded cloud evaluator mappings: `_EVALUATORS_NEEDING_CONTEXT` now includes `relevance` and `retrieval`; `_EVALUATORS_NEEDING_TOOL_CALLS` now includes `tool_selection`, `tool_input_accuracy`, `tool_output_utilization`, `tool_call_success`.
- Added default input mappings for all new evaluators in `_default_foundry_input_mapping()`.
- `agentops init` now scaffolds HTTP scenario starter files:
  - `run-http-model.yaml` - HTTP model-direct run config.
  - `run-http-rag.yaml` - HTTP RAG run config.
  - `run-http-agent-tools.yaml` - HTTP agent-with-tools run config (with `tool_calls_field`).
  - `bundles/agent_http_baseline.yaml` removed (replaced by scenario-specific bundles).
- Add `docs/tutorial-http-agent.md` - end-to-end tutorial for the Agent Framework / ACA scenario.
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
- Migrate versioning from static `pyproject.toml` field to `setuptools-scm` - version is now derived automatically from git tags.
- Redesign release pipeline into three workflow files:
  - `_build.yml` - reusable build workflow (test + package via setuptools-scm)
  - `staging.yml` - `release/*` branch pushes publish to TestPyPI and verify install
  - `release.yml` - `v*` tag pushes publish to TestPyPI, then PyPI (with approval gate), then create GitHub Release
- Add CLI smoke test in staging/release verify step (`agentops --version`, `agentops --help`, `agentops init`).
- Fix secret reference from `PIPY_TOKEN` to `PYPI_TOKEN`; add `TEST_PYPI_TOKEN` for TestPyPI.
- Add consistent workflow index header across all CI/CD workflow files.
- Add VSIX extension packaging and publishing to CI/CD pipeline; include Copilot skills in the VS Code Marketplace extension.


## [0.1.0] - 2026-__-__

### Added
- `DatasetFormat.context_field` - optional field to declare the JSONL column holding retrieved context documents; used by `GroundednessEvaluator` in both cloud and local evaluation modes.
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