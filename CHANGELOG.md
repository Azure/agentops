# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.16] - 2026-06-09

## [0.3.14] - 2026-06-09

### Added
- **`agentops assert run` orchestrates the open-source ASSERT framework.**
  AgentOps now invokes the `assert-ai` CLI as an active CI step instead of only
  consuming pre-generated artifacts via `assert_path:`. A new `assert:` block in
  `agentops.yaml` (`config`, `results_dir`, `suite`, `run_id`,
  `fail_on_violations`) drives subprocess invocation, locates the run output
  under `<results_dir>/<suite>/<run>/`, parses `metrics.json` and
  `scores.jsonl`, and writes a normalized summary at `.agentops/assert/latest.json`
  that the release evidence pack ingests automatically. Exit code 2 when any
  policy dimension reports violations.
- **`agentops redteam run` orchestrates Foundry's AI Red Teaming agent (PyRIT).**
  AgentOps now invokes `azure.ai.evaluation.red_team.RedTeam` against the
  configured target (Azure OpenAI deployment, Foundry prompt agent, or HTTP
  endpoint) and normalizes the per-category and per-strategy attack outcomes.
  A new `redteam:` block in `agentops.yaml` (`target`, `risk_categories`,
  `attack_strategies`, `num_objectives`, `fail_on_attack_success_rate`)
  controls the scan; results land at `.agentops/redteam/latest.json` so the
  evidence pack picks them up via `redteam_path:` automatically. Exit code 2
  when attack-success-rate exceeds the configured threshold.

## [0.3.13] - 2026-06-09

### Fixed
- **Quickstart rubrics no longer block azd eval runs with placeholder evidence.**
  The Travel Agent hardening flow now defaults to multi-turn dataset coverage and
  treats rubric evaluators as advanced opt-in only after Foundry / azd emits real
  metric names, while AgentOps preserves rubric metadata without failing a normal
  azd result solely because matching rubric metrics were not emitted.

## [0.3.12] - 2026-06-09

### Added
- **Foundry observability readiness now spans eval, Doctor, Cockpit, and release evidence.**
  `agentops.yaml` supports `dataset_kind`, `rubrics`, and `observability`
  metadata for multi-turn coverage, rubric evaluator gates, trace sampling, and
  replay/evaluation/dataset links. Doctor and Cockpit surface the readiness
  state without mutating cloud resources, and release evidence records the same
  signals for reviewers.
- **Trace promotion preserves evaluation lineage.** `agentops eval
  promote-traces` now carries operation/span IDs, source system, agent version,
  replay/evaluation URLs, sampling policy, and multi-turn message fields into
  candidate datasets and their manifest.

### Changed
- **Rubric evaluators are executed through the azd backend.** When `rubrics:`
  is configured, `agentops eval init` includes those evaluator names in the azd
  recipe and `agentops eval run` fails closed outside `execution: azd`, so rubric
  scores cannot be treated as evidence unless Foundry / azd actually ran them.
- **Tutorials now carry rubric and observability proof into evaluation and CI/CD.**
  The Travel Agent flow keeps the existing smoke recording through step 10, then
  upgrades the gate to multi-turn dataset rows, rubric thresholds, trace
  sampling/replay lineage, and CI/CD workflows that reuse the same eval contract.

## [0.3.11] - 2026-06-08

### Fixed
- **Local AI-assisted evaluators now support reasoning-model graders.** When
  `AZURE_OPENAI_DEPLOYMENT` points at `gpt-5*`, `o1*`, `o3*`, or `o4*`,
  AgentOps marks the Azure AI Evaluation evaluator model as reasoning-capable so
  the SDK sends `max_completion_tokens` instead of the unsupported `max_tokens`.
- **`agentops eval run` no longer hides interactive azd prompts while appearing
  to hang.** The azd backend now runs `azd ai agent eval run` and the follow-up
  `show` command with `--no-prompt` and a closed stdin, so any missing
  authentication/configuration fails visibly instead of waiting indefinitely.
- **`agentops eval init` now bootstraps the minimal azd prompt-agent context.**
  For Foundry prompt-agent configs, the command creates missing `azure.yaml` and
  `src/<agent>/agent.yaml` files, enriches the active `.azure/<env>/.env` with
  Foundry project metadata when it can resolve the project resource, and then
  generates the azd eval recipe. The prompt-agent quickstart now keeps the main
  path to `agentops eval init` followed by `agentops eval run`, while still using
  `azd ai agent eval` under the hood.
- **`agentops workflow analyze` now tells azd eval users to run
  `agentops eval init` first.** When an azd eval recipe is selected, the
  recommended commands and next steps now explicitly create or reuse the recipe
  before telling users to run the local eval gate.
- **`agentops eval run` now prints heartbeat feedback while azd is running.**
  Long `azd ai agent eval run` calls now show an immediate waiting message and
  periodic elapsed-time updates instead of leaving the terminal silent while
  Foundry completes the native evaluation.

## [0.3.10] - 2026-06-08

### Fixed
- **Prompt-agent tutorial now uses `azd ai agent eval` as the standard eval
  path.** Step 10 creates the minimal azd service context, records Foundry
  project metadata in the active `.azure/<env>/.env`, runs `agentops eval init`,
  and verifies the native azd eval backend with `agentops eval run`.
- **`agentops eval init` now prepares azd-compatible inputs for prompt-agent
  datasets.** The wrapper writes an azd JSONL copy with `query` values derived
  from AgentOps `input`, passes absolute paths for azd service-project
  resolution, uses stable built-in evaluators by default, and decodes azd output
  safely on Windows.
- **AgentOps now normalizes the current azd preview eval output.** The azd
  runner reads text run IDs, exports details with `azd ai agent eval show
  --out-file`, and converts per-criteria pass counts into aggregate metrics for
  the AgentOps threshold gate.

## [0.3.9] - 2026-06-08

### Added
- **AgentOps can now delegate Foundry eval execution to `azd ai agent eval`.**
  Projects with an azd `eval.yaml` recipe can set `execution: azd` and
  `eval_recipe: eval.yaml`; AgentOps invokes azd, normalizes emitted metrics
  into `results.json`, binds thresholds including Rubric/custom dimensions, and
  fails closed when configured thresholds do not map to emitted metrics.
- **Governance evidence support for ASSERT, ACS, and red-team readiness.**
  `agentops.yaml` can reference `assert_path`, `acs_path`, and `redteam_path`.
  Doctor, Cockpit, and release evidence record path, SHA-256 hash, status, and
  ACS checkpoint coverage without executing ASSERT, applying ACS controls, or
  exposing red-team payload text.
- **`agentops-governance` coding-agent skill.** The new skill drafts safe
  evidence templates for ASSERT policies, ACS contracts, Guided Guardrail review
  notes, and red-team readiness plans while explicitly refusing offensive
  payload generation.

### Fixed
- **Prompt-agent tutorial now explicitly verifies the Travel Agent dataset path
  after `agentops init`.** Step 7 now tells users to confirm
  `agentops.yaml` points at `.agentops/data/travel-smoke.jsonl` and provides a
  repair command if the wizard left the starter `.agentops/data/smoke.jsonl`.
- **`agentops eval init` now reuses configured prompt-agent inputs and avoids
  hidden azd prompts.** When `--dataset` is omitted, the command passes the
  existing `agentops.yaml` dataset to `azd ai agent eval init`. It also runs azd
  with `--no-prompt`, passes the configured Foundry project endpoint, agent
  name, prompt file, and bootstrap model, and prints a progress line before the
  potentially long Foundry initialization.
- **Prompt-agent tutorial guidance now keeps azd eval recipes advanced-only.**
  Step 10 now follows `workflow analyze` for the quickstart's AgentOps cloud
  eval path and explains that `agentops eval init` requires a full azd AI agent
  project context before `azd ai agent eval run` can resolve the Foundry
  project.
- **`agentops eval init` now prints safely on Windows terminals without Unicode
  support.** The CLI falls back to an ASCII updated marker instead of raising a
  `UnicodeEncodeError` on cp1252 consoles after it wires `execution: azd` and
  `eval_recipe`.
- **Foundry RBAC preflight now prevents the portal build-agent permission
  block.** The prompt-agent, hosted-agent, and end-to-end tutorials plus the
  packaged `agentops-eval` skill now grant `Foundry User` and `Cognitive
  Services OpenAI User` on the parent AI Services account using stable role IDs.
  This covers the Foundry UI's "You don't have permission to build agents"
  failure as well as the evaluator chat-completions data-plane failure, while
  still assigning the OpenAI role to Foundry/Azure AI managed identities used by
  server-side graders.

## [0.3.8] - 2026-06-04

### Fixed
- **`agentops init` now handles blank required wizard values gracefully.** If
  the user presses Enter without an existing Foundry endpoint or agent default,
  the wizard explains that AgentOps needs the missing value and re-prompts
  instead of proceeding to a later persistence failure. Scripted blank flags
  such as `--agent ""` now exit with the same friendly message and no traceback.
- **`agentops init` no longer depends on undeclared PyYAML.** The setup wizard
  now reads and writes `agentops.yaml` through the repository's `ruamel.yaml`
  helpers, fixing the ugly `No module named 'yaml'` traceback seen in clean
  installs.

### Changed
- **AgentOps brand tagline sequence now reads `Evaluate :: Ship :: Observe ::
  Own`.** The startup/explain banner now matches the intended product story
  order.

## [0.3.7] - 2026-06-01

### Fixed
- **RBAC preflight now covers Foundry/Azure AI managed identities, not only
  the signed-in user.** Cloud evaluations run server-side and some agent or
  grader calls authenticate as the managed identities on the backing AI
  Services account and child Foundry project. Granting `Cognitive Services
  OpenAI User` only to the user still allowed intermittent grader
  `AuthenticationError` failures and the v0.3.6 execution warning. The
  prompt-agent, hosted-agent, and end-to-end tutorials plus the
  `agentops-eval` skill now assign the same data-plane role to every managed
  identity in the Foundry resource group, preventing the warning/failure path
  before `agentops eval run`.

## [0.3.6] - 2026-06-01

### Changed
- **`agentops eval run` now distinguishes a grader *execution* failure from a
  quality-gate failure.** When evaluator workers error out on a subset of rows
  (auth/RBAC/timeout), no row has every grader return a score, so
  `items_passed_all` is `0` and the run reports `Threshold status: FAILED` even
  though every threshold that *could* be computed passed. The CLI now detects
  this case (errored graders combined with all thresholds passing) and prints a
  `Warning` explaining that this is an execution error, not a quality
  regression, names the most common cause (data-plane RBAC granted moments
  earlier that is still propagating to the evaluator workers), surfaces the
  first underlying grader error, and advises waiting a few minutes before
  re-running. The exit-code contract is unchanged. Added the
  `_grader_error_summary` helper plus focused unit tests.
- **Corrected the RBAC propagation guidance in the tutorials and the
  `agentops-eval` skill.** Data-plane role assignments on Cognitive Services
  accounts can take several minutes (not 30-120 seconds) to reach the
  independent, per-row evaluator workers, which can produce an *intermittent*
  `FAILED` with otherwise-green thresholds on the first run after granting
  access. The prompt-agent, hosted-agent, and end-to-end tutorials and the
  skill now describe this symptom and tell readers to wait and re-run rather
  than lower thresholds.

## [0.3.5] - 2026-06-01

### Changed
- **`agentops-eval` coding-agent skill now preflights the data-plane RBAC
  step that the Foundry portal does not assign by default.** Creating a
  Foundry project through the portal only grants the user `Foundry User`
  at the *project* scope, which does not cover
  `Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action`
  on the parent AI Services account where chat completions actually live.
  Subscription `Owner` is also insufficient because the built-in `Owner`
  role definition has `actions: ["*"]` but `dataActions: []`. The first
  `agentops eval run` against a fresh workspace therefore failed with
  `PermissionDenied` on every AI-assisted evaluator and every cloud-eval
  grader. The skill's new **Step 0.5 - Ensure data-plane RBAC on the AI
  Services account** resolves the Foundry project endpoint from
  `.azure/<env>/.env` or `.agentops/.env`, looks up the backing AI
  Services account + resource group with
  `az cognitiveservices account list`, fetches the signed-in object ID
  with `az ad signed-in-user show`, and runs an idempotent
  `az role assignment create` for `Cognitive Services OpenAI User` at
  the resource-group scope before handing off to `agentops eval analyze`.
  This keeps the skill experience consistent with the new manual
  instructions added to the prompt-agent, hosted-agent, and end-to-end
  tutorials, so users running the skill against a fresh Foundry project
  no longer hit the same 401 the manual tutorials previously hid.

## [0.3.4] - 2026-06-01

### Fixed
- **`agentops eval run` in local execution mode no longer fails with
  `Missing environment variables: AZURE_OPENAI_ENDPOINT` when only the
  Foundry project endpoint is configured.** `CONTRIBUTING.md` and the
  user-facing env-var docs both stated that `AZURE_OPENAI_ENDPOINT` is
  "auto-derived from the project endpoint when absent", but
  `pipeline/runtime.py::_model_config` only read the explicit
  `AZURE_OPENAI_ENDPOINT` env var with no fallback — so a fresh workspace
  created by `agentops init` (which writes `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`
  but not `AZURE_OPENAI_ENDPOINT`) would always trip the missing-env error
  the first time AI-assisted evaluators tried to run locally. The new
  helper `agentops.utils.azure_endpoints.derive_openai_endpoint_from_project`
  trims the trailing `/api/projects/<name>` segment from a Foundry project
  URL (covering both `services.ai.azure.com` and the legacy
  `cognitiveservices.azure.com` hosts) to recover the AI Services account
  base URL, which is exactly what the `openai` and `azure-ai-evaluation`
  SDKs want. `_model_config` now uses the derived value as a fallback
  whenever `AZURE_OPENAI_ENDPOINT` is unset, so the documented behavior
  finally matches the runtime. When `AZURE_OPENAI_DEPLOYMENT` is the only
  thing missing, the error message now points users at the deployment list
  in the Foundry portal *and* mentions the `execution: cloud` escape hatch
  in `agentops.yaml` so the next step is obvious without leaving the
  terminal.

## [0.3.3] - 2026-05-31

### Changed
- **Runtime dependencies now have upper bounds so a future SDK major release
  cannot silently break installs.** `pyproject.toml` previously declared every
  Azure-SDK dependency with only a lower bound (e.g. `azure-ai-projects>=2.0.1`),
  so `pip install agentops-accelerator` could resolve `azure-ai-projects 3.x`
  the day after that ships and break the agent-definition serialization (the
  exact failure mode that produced the `invalid_payload — Required properties
  ["kind"] are not present` regression below). Each Azure SDK dependency
  (`azure-ai-projects`, `azure-ai-evaluation`, `azure-identity`, `azure-monitor-*`,
  `azure-mgmt-*`) is now constrained to its current major. `pandas`, `fastapi`,
  `uvicorn`, `httpx`, and `markdown` are similarly capped to their next major.
  `cryptography` is intentionally left unbounded so security patches can flow
  through without a coordinated AgentOps release. Lift any of these bounds via
  an explicit PR that exercises the new SDK against `tests/`.

- **`agentops workflow generate` now stamps the installed agentops version
  into generated CI/CD templates instead of always installing from
  `git+...@main`.** Every generated `agentops-pr.yml`, `agentops-deploy-*.yml`,
  `agentops-watchdog.yml` (and their Azure DevOps pipeline equivalents) used to
  contain `pip install "agentops-accelerator[...] @ git+https://github.com/Azure/agentops.git@main"`,
  with no version pin and a stale "NOTE: pinned to GitHub main until the next
  package release" comment. User CI runs were therefore non-reproducible: the
  same workflow file pulled different agentops snapshots day to day, which is
  how PO's recorded tutorial took a hard SDK regression mid-record. The
  generator now writes a literal `==X.Y.Z` pin derived from the agentops version
  currently installed on the machine running `agentops workflow generate` — so
  a user who generates workflows against AgentOps `0.3.3` always installs
  `agentops-accelerator==0.3.3` on every CI run, and `agentops-accelerator`
  brings exact-major Azure SDKs along (per the upper bounds above). Editable
  installs (versions carrying a local segment like `+gabcdef` or marked
  `.devN`) keep the `@main` fallback so contributors testing template changes
  still get a resolvable install. Existing user workflows are unaffected until
  the user re-runs `agentops workflow generate --force` against a release of
  AgentOps that ships this change.

### Fixed
- **Doctor regression check no longer flags the previous PR run as "current"
  in CI.** The results-history loader (`agent/sources/results_history.py`)
  was reading the wrong fields from `results.json` and excluding
  `.agentops/results/latest/` from the candidate list. Three coordinated
  schema-alignment fixes restore correctness:
  1. `_summarize` now reads top-level `aggregate_metrics` first (the field
     the orchestrator actually writes, per `core/results.py`), then falls
     back to legacy `metrics`/`run_metrics`. Previously the loader looked
     only at the legacy fields, so every freshly-written local
     `RunSummary` had `metrics = {}` and the regression check could never
     see the current run's metrics.
  2. `_summarize` now reads `summary.overall_passed` first when deriving
     the `run_pass` flag, then falls back to the legacy `summary.run_pass`
     / `metrics.run_pass` shapes.
  3. `_summarize` now orders runs by `timestamp` → `finished_at` →
     `started_at` → `created_at` → `summary.timestamp`. The previous list
     omitted `finished_at`/`started_at`, which are the two fields
     `results.json` actually contains, so every loaded run defaulted to
     epoch-zero ordering.
  4. `_collect_local_runs` now includes `.agentops/results/latest/` when it
     is the only local results directory. In CI, generated workflows run
     `agentops eval run --output .agentops/results/latest` and write
     nowhere else; the old loader unconditionally skipped `latest/` for
     dev-mode dedup, so in CI `local_runs` was always empty. With cloud
     listing trailing behind by seconds (eventual consistency), the
     regression check would then compute `latest = previous_run` and
     blame the just-completed candidate's coherence/groundedness on the
     prior PR. Dev-mode dedup is preserved: when a timestamped sibling
     exists, `latest/` is still skipped.
- **Prompt-agent deploy: `stage` no longer fails with `Required properties ["kind"] are not present` against `azure-ai-projects` 2.x.**
  `_copy_definition` previously called `.copy()` on the typed
  `PromptAgentDefinition` returned by `get_version`. In SDK 1.x that
  preserved the typed model so the body serialized as a flat
  `{"kind": "prompt", "model": ..., "instructions": ...}`. In SDK 2.x
  the same `.copy()` returns a stripped base `Model` whose JSON shape
  is `{"_data": {"kind": "prompt", ...}}`, and `.get("kind")` returns
  `None` — so the request body that reached the Foundry Agents service
  contained `definition: {"_data": {...}}` with no top-level `kind`,
  and the service rejected it with `invalid_payload`. This regression
  only fired on the `created` action path (i.e. when the user's prompt
  differed from the seed); the `reused` and bootstrap paths were
  unaffected because they don't round-trip the typed model through
  `.copy()`. `_copy_definition` now normalizes any SDK definition
  object to a plain `dict` before mutation, and `_create_agent_version`
  no longer puts a root-level `kind` on the request body (the new API
  treats `kind` strictly as the discriminator inside `definition`).
- **Tutorial: prompt-agent step 13 now shows the steady-state `foundry-agent.json` (action: reused) instead of the bootstrap edge case.**
  The example JSON in step 13 previously showed `action: bootstrapped`
  with `candidate_agent: "travel-agent:1"` and a "the two numbers are
  expected to differ until the environment has caught up to the seed"
  explanation. In practice the merge-triggered deploy is almost never
  the run that bootstraps — by the time the user reaches step 13, the
  skill's verification dispatch in step 12 plus the first PR run have
  already settled dev to `travel-agent:2`, so the merge deploy reports
  `action: reused` with `candidate_agent: "travel-agent:2"` (matching
  `source_agent`). The example now shows the steady-state shape (taken
  from a real recording), uses the runner-resolved absolute paths the
  user actually sees (`/home/runner/work/<your-repo>/...`), and uses a
  real 64-char `prompt_sha256` + a real ISO timestamp. The
  three-outcome list (`reused` / `created` / `bootstrapped`) below the
  JSON keeps the bootstrap case as the documented edge condition.
- **Tutorial: prompt-agent step 13 now matches what the workflow skill actually does (dispatches both workflows).**
  PR #211 mistakenly narrowed the step 13 callout to say the workflow
  skill only dispatches `agentops-pr.yml` as a verification run, based
  on incorrect reasoning about `push:` triggers (the skill actually
  uses `workflow_dispatch`, which works against any branch regardless
  of the workflow's `push:` block). In practice — verified against a
  live recording — the skill dispatches **both** `agentops-pr.yml`
  and `agentops-deploy-dev.yml` end-to-end as part of CI verification,
  asking the user to approve first per SKILL.md rule #14. The step 13
  callout now reflects this and explains the expected outcome (both
  runs may exit `threshold_failed` on first contact with an empty dev
  project because the bootstrap path produces a fresh `travel-agent:1`
  that has not been measured against the seed thresholds yet — by
  design, not a CI wiring failure). The "What you should see in the
  first PR workflow run" section also updates from the
  "dev is still empty" assumption (which becomes false after the
  skill's verification dispatch) to the three possible outcomes
  (`reused` / `created` / `bootstrapped`) you can actually see at this
  point. The "After the merge" paragraph now calls out that the
  merge-triggered deploy is the **second** deploy-dev run for the
  repo, not the first.
- **Tutorials: end-to-end audit caught misleading dist URLs, phantom CLI commands, missing JSON fields, and stale Doctor advisory text.**
  All three tutorials previously installed the development build from a
  personal fork URL (`git+https://github.com/placerda/agentops.git@develop`);
  they now point at the canonical
  `git+https://github.com/Azure/agentops.git@develop`. The prompt-agent
  tutorial referenced a non-existent `prompt_deploy record` subcommand in
  two places — the actual command is `prompt_deploy summarize`, matching
  `src/agentops/pipeline/prompt_deploy.py` and the deploy template's
  `Mark candidate as deployed` step. The same tutorial's `foundry-agent.json`
  sample was missing the `eval_config` field that the code writes at
  `src/agentops/pipeline/prompt_deploy.py:186`. The step 12 skill prompt
  and the step 13 prose did not tell the reader to rewrite the dev-deploy
  trigger from `develop` to `main` for this trunk-on-`main` tutorial; the
  generator's stock default is `develop`, which would silently no-op after
  the first merge. Step 12 now instructs the skill to do the rewrite (and
  the bullet list of skill actions calls it out as a required step, with
  a manual-edit fallback). Step 13's "deploy fires automatically on `main`"
  sentence now states the dependency on the step 12 rewrite explicitly,
  and the placeholder phrase "your trunk branch" is now disambiguated as
  "`main` in this tutorial". The end-to-end tutorial's step 5 and step 9
  Doctor descriptions still read as if Doctor were advisory-only in PR
  workflows — that text predates the `--doctor-gate critical` default;
  both blocks now describe the actual behavior (critical findings block
  the PR by default; warning/info are evidence-only).

### Changed
- **Tutorials: skip-if-skill callouts now state the skill's outcome directly and accurately.**
  The `step 13` callout in `docs/tutorial-prompt-agent-quickstart.md` and the
  baseline-run paragraph in `docs/tutorial-end-to-end.md` previously opened
  with "if you used the workflow skill, this is already done…" plus a
  manual-fallback block. That conditional framing was confusing because the
  preceding step (`step 12` of the prompt-agent tutorial, `step 5` of the
  end-to-end tutorial) only documents the workflow-skill path — there is no
  alternative wired-by-hand path the reader could have taken. Both callouts
  now state the skill's outcome directly, and the redundant `git add` /
  `commit` / `push` and `gh workflow run agentops-pr.yml --ref main` blocks
  have been removed (the skill already triggers the first run). A small
  `gh run list` / `gh run watch` snippet remains as an opt-in way to wait
  on the run from the terminal instead of the Actions UI. The previous
  wording also over-claimed that the skill triggered verification runs of
  **both** `agentops-pr.yml` and `agentops-deploy-dev.yml`; the skill only
  dispatches the PR workflow as a sanity check (`workflow_dispatch`), while
  `agentops-deploy-dev.yml` triggers on the first real merge into the trunk
  branch. The callout now reflects this accurately and notes that the
  deploy-dev run happens at the end of the section, not during the skill's
  setup.

## [0.3.1] - 2026-05-29

### Changed
- **Tutorials now flag the workflow skill's setup actions as redundant in the manual follow-up steps.**
  When users run the `agentops-workflow` skill in the CI-wiring step of either
  the prompt-agent tutorial (step 12) or the end-to-end tutorial (step 5, the
  same skill invocation that precedes the baseline-run step), the skill already
  commits the workspace, pushes `main` to GitHub, and triggers a first
  verification run of `agentops-pr.yml` (and `agentops-deploy-dev.yml` for the
  prompt-agent flow). The next step previously asked users to repeat all
  three actions, which was a no-op at best and confusing at worst (the
  `git add` would find nothing to commit, the `git push` would report
  up-to-date, the dispatched PR run would be the second one, not the first).
  Step 13 of `docs/tutorial-prompt-agent-quickstart.md` and the baseline-run
  paragraph in `docs/tutorial-end-to-end.md` now open with an explicit
  "if you used the workflow skill above, this is already done" callout and
  reframe the manual commands as a fallback for users who skipped the skill
  or wired CI by hand. The deliberate baseline-PR step that follows (open a
  feature branch, open a PR, merge once green) is unchanged — it must still
  go through a real pull request, which the skill does not do for you, so
  that the rolling Doctor history is seeded.
- **Tutorial wording: "quickstart" → "tutorial", "workshop" → "tutorial".**
  The three documentation entries that were labeled "Prompt Agent quickstart",
  "Hosted Agent quickstart", and "End-to-end workshop" now read as "Foundry
  Prompt Agent tutorial", "Hosted or HTTP Agent tutorial", and "End-to-end
  tutorial" across `README.md`, `plugins/agentops/README.md`, `AGENTS.md`,
  `docs/concepts.md`, `docs/doctor-explained.md`, the `agentops-workflow`
  skill (both synced copies), and the H1s + cross-references inside each
  tutorial doc. The README description for the end-to-end tutorial now also
  states explicitly that it **extends** either of the type-specific tutorials
  (sandbox → dev → qa → prod plus Foundry red-team scans plus
  trace-to-regression promotion) so the difference between the three is
  obvious at a glance. The "quickstart" framing no longer fits doc bodies
  that grew past 1000 lines covering multi-environment promotion, regression
  injection, Doctor evidence, and Cockpit. The tutorial **filenames are
  intentionally preserved** (`tutorial-*-quickstart.md`) to keep inbound
  links and bookmarks stable.
- **Skill + tutorial guidance now require `Cognitive Services OpenAI User` as a prerequisite RBAC role.**
  The `agentops-workflow` skill, `tutorial-prompt-agent-quickstart.md`,
  `tutorial-end-to-end.md`, and `docs/ci-github-actions.md` now instruct users
  to grant the OIDC/CI service principal **both** Foundry User on the Foundry
  project **and** Cognitive Services OpenAI User on the underlying Azure AI
  Services account that hosts the evaluator model deployment. Foundry
  `azure_ai_evaluator` graders impersonate the OIDC principal to call OpenAI;
  without the OpenAI User role they fail with a 401 `PermissionDenied` and
  every cloud eval metric returns `null`, blocking the first PR run. The skill
  now emits the matching `az role assignment create` commands for both roles
  (role ids `53ca6127-db72-4b80-b1b0-d745d6d5456d` and
  `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd`) before dispatching the workflow.

### Fixed
- **`agentops init --azd-env <name>` no longer pre-fills the endpoint from a different env.**
  When the user explicitly targets a new azd env (e.g. `--azd-env dev` while the
  active env is `sandbox`), the wizard now refuses to pre-fill
  `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` from sources that don't match the
  targeted env — process environment, legacy top-level `project_endpoint:` in
  `agentops.yaml`, or a *different* `.azure/<env>/.env` file. Instead it
  prompts with no default and prints a short note explaining where the
  suspect default came from (e.g. "the active azd env `sandbox`'s
  `.azure/sandbox/.env`"). This stops the silent sandbox→dev endpoint leak
  that surfaced when users ran the multi-env tutorials; values picked up
  from the targeted env's own `.azure/<env>/.env` are still honored. The
  strict check only fires when `--azd-env` is passed explicitly — bare
  `agentops init` keeps its existing best-effort default behavior.
- **Cloud eval surfaces grader execution errors instead of silent nulls.**
  When a Foundry `azure_ai_evaluator` grader fails to execute (most
  commonly because the evaluator service principal lacks
  `Cognitive Services OpenAI User` on the target model deployment), the
  per-metric `score` comes back `null` and the real cause is buried in
  `result.sample.error.message`. The cloud-results parser now lifts that
  message into `RowMetric.error` (including the error `code` prefix
  when present), so the actionable error appears in `results.json` and
  `report.md` instead of operators only seeing `actual=missing` in the
  threshold table. The orchestrator's "0 usable metric scores" warning
  also quotes the first grader error so CI logs carry the signal
  without operators having to download the raw artifact.

### Added
- **`cloud_output_items.json` is now uploaded as a CI artifact.** Generated PR and deploy workflows (GitHub Actions and Azure DevOps) include `.agentops/results/latest/cloud_output_items.json` in the `agentops-*-results` artifact bundle alongside `results.json`, `report.md`, and `cloud_evaluation.json`. Pairs with the "0 usable scores" warning so operators can diagnose unrecognized Foundry grader shapes without re-running locally.
- **`cloud_output_items.json` raw dump.** Every cloud eval run now
  writes the raw `output_items` it received from Foundry to
  `<output_dir>/cloud_output_items.json`, in addition to the parsed
  `results.json`. When a future grader / SDK upgrade changes the on-the-
  wire shape and the parser stops finding scores, the artifact bundle
  alone is enough to triage the issue. The orchestrator also emits an
  explicit warning to the progress channel when a cloud run yields zero
  usable metric scores despite returning rows, pointing the user at the
  new dump file.
- **`.gitattributes`** pinning `*.yml` / `*.yaml` / `*.sh` / `*.md` /
  `*.py` to LF line endings, preventing future CRLF↔LF churn from
  Windows clones with `core.autocrlf=true`. Normalizes the existing
  `_build.yml` and `ci.yml` (previously CRLF) to LF so all files in
  `.github/workflows/` share a single line-ending convention.

### Removed
- **Retired tombstone publish jobs from CI.** The `agentops-toolkit` →
  `agentops-accelerator` deprecation tombstones were one-shot publishes
  for v0.3.0 / v0.3.1; the `build-pypi-tombstone`,
  `publish-tombstone-testpypi`, `verify-tombstone-testpypi`,
  `publish-tombstone-pypi`, and `publish-tombstone-vsix(-prerelease)`
  jobs (plus their `cut-release.yml` plugin-version sync steps) have
  been removed from `release.yml`, `staging.yml`, and `cut-release.yml`.
  The `github-release` job now depends only on `publish-pypi` and
  `publish-vsix` (both required), and the dead `always()` guard has
  been dropped. Future releases ship only `agentops-accelerator` on
  PyPI and the `AgentOpsAccelerator.agentops-accelerator` VSIX.
  The orphaned `scripts/verify_tombstones.py` harness and
  `docs/verifying-tombstones.md` checklist (both one-shot tools
  whose CI counterpart no longer exists) have been removed, along
  with the now-unused `tombstones/pypi/` package source and the
  `tombstones/vscode/` extension source — only
  `tombstones/vscode/CDN_DEPRECATION_REQUEST.md` survives as the
  template for the still-pending Microsoft CDN deprecation request.

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

## [0.3.0] - 2026-05-28

### Added
- **Auto-bootstrap empty Foundry projects on first deploy.** New optional
  `prompt_agent_bootstrap` block in `agentops.yaml` lets the prompt-agent
  deploy workflow create the first version of an agent in a dev / qa / prod
  Foundry project that does not yet have one. When the stage step looks up
  the seed agent and gets a 404, it reads the model deployment (required)
  plus optional `description`, `model_parameters`, and `tools` from
  `prompt_agent_bootstrap`, combines them with `prompt_file`, and creates
  the first version automatically. The deployment artifact records the new
  `action: "bootstrapped"` for that first run; subsequent deploys follow
  the normal reuse / next-version flow. Eliminates the previous
  per-environment manual seeding step. `agentops workflow analyze` now
  warns when a prompt-agent workspace is missing this block. Authentication
  (401 / 403) and other non-404 errors continue to propagate — the
  bootstrap path only triggers on a genuine "agent does not exist" 404.
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