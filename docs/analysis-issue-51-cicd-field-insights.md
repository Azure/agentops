# Issue #51 — Review CI/CD Based on Field Insights

**Date:** 2026-04-03
**Issue:** https://github.com/Azure/agentops/issues/51
**Author:** placerda
**Reference repo:** https://github.com/hrprtkaur88/foundrycicdbasic

---

## 1. Executive Summary

This analysis evaluates how well AgentOps Toolkit serves as a CI/CD-ready
evaluation tool based on real-world pipeline patterns observed in Harpreet's
Foundry CI/CD reference repository. The goal is to identify what prevents teams
like Harpreet's from replacing their custom Python scripts with
`agentops eval run`, and what AgentOps must improve to be viable in real
CI/CD environments.

**Key finding:** AgentOps has strong CI/CD foundations (exit codes, artifacts,
declarative config, generated workflow) but is missing critical evaluator
coverage and data-source patterns that real-world pipelines require. A team
using Harpreet's pipeline today cannot switch to AgentOps without losing
evaluator coverage.

---

## 2. Task Analysis

### Task 1: Review Harpreet repository and pipeline structure

**What the repo is:**
A reference implementation showing how to create, test, evaluate, and red-team
Foundry agents using raw Python scripts orchestrated by CI/CD pipelines.

**Repository structure:**

```
foundrycicdbasic/
├── createagent.py                    # Creates a Foundry agent via Agent Framework SDK
├── exagent.py                        # Smoke-tests an existing agent with a real query
├── agenteval.py                      # Runs cloud evaluation via OpenAI Evals API
├── agenteval_classic.py              # Local evaluation fallback
├── redteam.py                        # Red-team safety evaluation
├── redteam_classic.py                # Red-team local fallback
├── requirements.txt                  # Unpinned runtime dependencies
├── sample.env                        # Example environment variables
├── data_folder/                      # Red-team taxonomy + output files
├── .github/workflows/
│   ├── create-agent-multi-env.yml    # GitHub Actions: deploy agent (dev→test→prod)
│   └── agent-consumption-multi-env.yml  # GitHub Actions: test→eval→redteam (dev→test→prod)
├── cicd/
│   ├── createagentpipeline.yml       # Azure DevOps: deploy agent
│   └── agentconsumptionpipeline.yml  # Azure DevOps: test→eval→redteam
└── cicd_patterns/
    └── foundry-cicd-workflow.pptx    # Presentation on patterns
```

**Pipeline flow (agent-consumption-multi-env.yml):**

```
build (validate syntax)
  → test-dev (exagent.py — smoke-test agent)
    → evaluate-test (agenteval.py — cloud evaluation)
      → red-team-test (redteam.py — safety evaluation)
        → verify-prod (exagent.py — production verification)
```

**Key observations:**

1. **All evaluation logic is imperative** — evaluator names, data mappings,
   test data, and testing criteria are hardcoded in Python scripts.
2. **No thresholds or gating** — every eval/redteam step uses
   `continue-on-error: true`. The pipeline never blocks on quality.
3. **Authentication uses service principal JSON blobs** — stored as
   `AZURE_CREDENTIALS_*` secrets, not OIDC.
4. **Dual platform** — same pipelines exist for both GitHub Actions and
   Azure DevOps (manually duplicated).
5. **Inline test data** — `agenteval.py` has query/response/tool_definitions
   hardcoded in the script, not in external data files.

### Task 2: Identify evaluation patterns used in real scenarios

The following evaluation patterns are used in Harpreet's pipeline. Each is
mapped to AgentOps support status.

#### Pattern A: Agent smoke test (exagent.py)

**What it does:** Retrieves an existing agent by name, sends a real query,
handles MCP approval requests, and prints the response with citations.

**Purpose in CI/CD:** Validates the agent is alive and responsive before
running expensive evaluations.

**AgentOps equivalent:** None. AgentOps has no "health check" or "smoke test"
concept. The `agentops eval run` command goes straight to evaluation.

**Gap severity:** Low. This is a convenience — users can add a custom step
before `agentops eval run` in their pipeline.

#### Pattern B: Cloud evaluation with inline data (agenteval.py)

**What it does:**
1. Creates an OpenAI client from the Foundry project client
2. Defines `data_source_config` with `type: custom` and an item schema
3. Defines `testing_criteria` — a list of `azure_ai_evaluator` entries
4. Calls `client.evals.create()` to create an eval group
5. Calls `client.evals.runs.create()` with inline JSONL data
6. Polls until completion
7. Retrieves output items

**Evaluators used:**

| Category | Evaluator | Builtin name | AgentOps support |
|---|---|---|---|
| System | Task Completion | `builtin.task_completion` | **Not supported** |
| System | Task Adherence | `builtin.task_adherence` | **Not supported** |
| System | Intent Resolution | `builtin.intent_resolution` | **Not supported** |
| RAG | Groundedness | `builtin.groundedness` | Supported |
| RAG | Relevance | `builtin.relevance` | **Not supported** |
| Process | Tool Call Accuracy | `builtin.tool_call_accuracy` | Supported |
| Process | Tool Selection | `builtin.tool_selection` | **Not supported** |
| Process | Tool Input Accuracy | `builtin.tool_input_accuracy` | **Not supported** |
| Process | Tool Output Utilization | `builtin.tool_output_utilization` | **Not supported** |

**Data format used:**
- `query`: array of message objects (system + user messages)
- `response`: array of message objects (assistant + tool_call + tool_result)
- `tool_definitions`: array of tool schemas
- `tool_calls`: null (derived from response)

**AgentOps data format:**
- `input`: string (simple text field from JSONL)
- `expected`: string (simple text field from JSONL)
- `context`: optional string

**Gap severity:** **Critical.** 7 of 9 evaluators used in the field are not
supported by AgentOps. The data format is also incompatible — Harpreet uses
conversation-format arrays while AgentOps expects simple string fields.

#### Pattern C: Red-team / safety evaluation (redteam.py)

**What it does:**
1. Creates an agent version via `project_client.agents.create_version()`
2. Defines safety testing criteria:
   - `builtin.prohibited_actions`
   - `builtin.task_adherence`
   - `builtin.sensitive_data_leakage`
   - `builtin.self_harm`
   - `builtin.violence`
   - `builtin.sexual`
   - `builtin.hate_unfairness`
3. Creates evaluation taxonomy via `project_client.evaluation_taxonomies.create()`
4. Creates eval run with `data_source.type: azure_ai_red_team`
5. Uses `attack_strategies: ["Flip", "Base64"]` with generated adversarial inputs
6. Polls until completion, saves results to JSON

**AgentOps equivalent:** None. AgentOps has no concept of:
- Red-team data sources (`azure_ai_red_team`)
- Safety evaluators (prohibited_actions, sensitive_data_leakage, violence, etc.)
- Attack strategies
- Evaluation taxonomies

**Gap severity:** **High.** Red-team testing is a major field requirement.
However, this may be better addressed as a separate `agentops redteam` command
rather than extending `agentops eval run`, since the data source model is
fundamentally different (generated adversarial inputs vs. user-provided JSONL).

#### Pattern D: Multi-environment sequential deployment

**What it does:** Runs the same scripts across dev → test → prod environments,
with each stage depending on the previous. Production requires manual approval
via GitHub Environment protection rules.

**AgentOps equivalent:** Not directly relevant to the AgentOps tool — this is
a pipeline orchestration pattern. AgentOps's `project_endpoint_env` config
already supports being called in different environments by varying the
endpoint secret. No tool change needed.

**Gap severity:** None for the tool. Documentation gap only.

#### Pattern E: Scheduled security scans

**What it does:** Weekly cron trigger (`0 2 * * 1`) runs the full
test → eval → redteam pipeline on Monday mornings.

**AgentOps equivalent:** Not relevant to the tool — this is a pipeline trigger
pattern. `agentops eval run` works fine when invoked by a cron job.

**Gap severity:** None for the tool. Documentation gap only.

### Task 3: Define supported CI/CD integration models

Based on field analysis, AgentOps should support these integration models:

| Model | Description | Tool readiness |
|---|---|---|
| **PR gating** | `agentops eval run` in a PR workflow; exit code 2 blocks merge | **Ready** — implemented and documented |
| **Scheduled regression** | Cron-triggered eval run to detect drift | **Ready** — CLI works, needs documentation |
| **Post-deployment validation** | Run eval after deploying to an environment | **Ready** — CLI works, needs documentation |
| **Multi-config matrix** | Run multiple eval configs in parallel | **Ready** — documented with matrix strategy |
| **Advisory mode** | Run eval and report results without blocking | **Partially ready** — exit code 2 blocks; no `--no-fail` flag |

### Task 4: Define best practices for gating deployments based on evaluations

**What AgentOps provides today:**

| Capability | Status | Evidence |
|---|---|---|
| Exit code contract (0/1/2) | Implemented | `cli/app.py` raises `typer.Exit(code=2)` on threshold failure |
| Declarative thresholds in YAML | Implemented | `bundles/*.yaml` with `thresholds[]` |
| Per-metric threshold criteria | Implemented | `>=`, `>`, `<=`, `<`, `==`, `true`/`false` in `thresholds.py` |
| Per-row threshold evaluation | Implemented | `runner.py` `_evaluate_item_thresholds()` |
| PR comment with report | Implemented | Workflow template posts/updates PR comment |
| Job summary | Implemented | Workflow writes to `$GITHUB_STEP_SUMMARY` |
| Artifacts on failure | Implemented | `if: always()` on artifact upload step |

**What's missing for real-world gating:**

| Gap | Impact |
|---|---|
| No `--no-fail` / `--advisory` flag | Teams can't run eval in "observe only" mode (like Harpreet's `continue-on-error`) |
| `agentops config validate` not implemented | Teams can't fail-fast on bad config before running expensive evaluations |
| No threshold on safety evaluators | Can't gate on red-team results since safety evaluators aren't supported |

### Task 5: Identify gaps in current CLI for CI/CD usage

| Gap | Category | Severity | Detail |
|---|---|---|---|
| Missing cloud evaluators | Evaluator coverage | **Critical** | 7 of 9 evaluators used in field are unsupported: `task_completion`, `task_adherence`, `intent_resolution`, `relevance`, `tool_selection`, `tool_input_accuracy`, `tool_output_utilization` |
| No conversation-format data | Data model | **High** | Field uses array-of-messages for query/response; AgentOps only supports simple string fields |
| No red-team support | Feature | **High** | No safety evaluators, no `azure_ai_red_team` data source, no attack strategies |
| No `--no-fail` flag | CLI | **Medium** | Can't run in advisory mode without `continue-on-error` in the pipeline YAML |
| `config validate` not implemented | CLI | **Medium** | Can't pre-validate configs in CI before running eval |
| `dataset validate` not implemented | CLI | **Medium** | Can't verify dataset integrity in CI |
| No Azure DevOps template | Documentation | **Low** | `agentops config cicd` only generates GitHub Actions; ADO users must write their own |

---

## 3. Acceptance Criteria Assessment

### AC 1: CI/CD integration patterns are clearly defined

**Verdict: PARTIALLY MET**

**What exists:**
- `docs/ci-github-actions.md` — comprehensive guide covering triggers, auth,
  exit codes, artifacts, PR comments, job summary, troubleshooting
- Generated workflow template via `agentops config cicd`
- Matrix strategy documentation for multi-config runs
- Internal CI/CD workflows documented for contributors

**What's missing:**
- No documentation for Azure DevOps integration
- No documentation for "advisory mode" (run without gating)
- No documentation for scheduled evaluation pattern
- The patterns are defined for the *simple case* (model-direct with similarity)
  but not for the *real-world case* (agent evaluation with process/system
  evaluators)

**To close:** Document Azure DevOps integration pattern. Document advisory
mode. Ensure patterns cover agent evaluation scenarios, not just model-direct.

### AC 2: Pipelines support evaluation as a gating mechanism

**Verdict: MET (for supported evaluators)**

**Evidence:**
- Exit code 0/1/2 contract is implemented and tested
- Workflow template uses `exit $EXIT_CODE` — non-zero fails the job
- Threshold evaluation supports multiple criteria operators
- Per-row and aggregate threshold evaluation is implemented
- CLI propagates exit code 2 via `raise typer.Exit(code=2)`

**Caveat:** Gating only works for the evaluators AgentOps supports. Since most
field-used evaluators are unsupported, the gating mechanism exists but can't
be applied to the metrics teams actually care about (task_completion,
intent_resolution, etc.).

### AC 3: Exit codes are correctly interpreted in CI/CD

**Verdict: MET**

**Evidence:**
- Workflow template maps exit codes to step summary messages
  (0 → pass, 2 → threshold fail, else → error)
- Exit code saved to `$GITHUB_OUTPUT` for downstream consumption
- `test_cicd.py` asserts `EXIT_CODE` and `exit $EXIT_CODE` are in template
- GitHub Actions natively fails on non-zero — no special handling needed
- Exit code semantics documented in `docs/ci-github-actions.md`

### AC 4: Artifacts are generated and usable in pipeline context

**Verdict: MET**

**Evidence:**
- Workflow uploads 6 artifact files: `results.json`, `report.md`,
  `backend_metrics.json`, `cloud_evaluation.json`, `backend.stdout.log`,
  `backend.stderr.log`
- Upload uses `if: always()` — artifacts available even on failure
- `results.json` has versioned Pydantic schema — machine-readable
- `report.md` is human-readable and posted as PR comment
- `cloud_evaluation.json` includes `report_url` for Foundry portal deep-link
- `agentops report --in results.json` can regenerate reports from artifacts

### AC 5: At least one reference pipeline is documented

**Verdict: MET**

**Evidence:**
- `docs/ci-github-actions.md` is a complete reference pipeline guide
- `agentops config cicd` generates a tested, ready-to-use workflow
- Template includes inline comments explaining every step
- Quick start, auth setup, customization, and troubleshooting covered

### AC 6: Integration works with real-world scenarios

**Verdict: NOT MET**

**Evidence from field analysis:**

Harpreet's pipeline represents a real-world scenario. To replace their
`agenteval.py` with `agentops eval run`, a user would need to:

1. **Define evaluators in a bundle YAML** — but 7 of 9 evaluators they use
   are not supported by AgentOps
2. **Provide test data in JSONL** — but the field uses conversation-format
   arrays (query as message list, response as message list with tool calls),
   while AgentOps expects simple string fields
3. **Get evaluation results** — AgentOps produces `results.json` and
   `report.md`, which is better than Harpreet's raw stdout, but the results
   won't contain the metrics teams need
4. **Gate on results** — AgentOps has threshold gating, which Harpreet's
   pipeline lacks, but it can only gate on supported evaluators

**What a user would need to do today to use AgentOps in Harpreet's pipeline:**

```yaml
# What they want to write:
bundle:
  evaluators:
    - name: TaskCompletionEvaluator     # ❌ not supported
    - name: TaskAdherenceEvaluator      # ❌ not supported
    - name: IntentResolutionEvaluator   # ❌ not supported
    - name: GroundednessEvaluator       # ✅ supported
    - name: RelevanceEvaluator          # ❌ not supported
    - name: ToolCallAccuracyEvaluator   # ✅ supported
    - name: ToolSelectionEvaluator      # ❌ not supported

# What they can actually use today:
bundle:
  evaluators:
    - name: GroundednessEvaluator       # ✅
    - name: ToolCallAccuracyEvaluator   # ✅
    # ...that's it
```

**Blockers preventing real-world adoption:**

| Blocker | Why it blocks |
|---|---|
| Missing evaluators | Teams can't measure what matters to them |
| String-only data format | Teams can't provide conversation-format test data |
| No red-team | Teams must maintain a separate `redteam.py` alongside AgentOps |

---

## 4. Gap Prioritization for Closing the Issue

### Priority 1 — Critical (blocks AC 6)

| Item | What to do | Effort |
|---|---|---|
| Add system evaluators | Add `task_completion`, `task_adherence`, `intent_resolution` to `_cloud_evaluator_data_mapping` | Low — mapping only, no new API calls |
| Add RAG evaluator: relevance | Add `relevance` alongside existing `groundedness` | Low |
| Add process evaluators | Add `tool_selection`, `tool_input_accuracy`, `tool_output_utilization` to `_EVALUATORS_NEEDING_TOOL_CALLS` or a new set | Low-Medium — need to verify data_mapping for each |

These evaluators all use the same `azure_ai_evaluator` type and
`builtin.<name>` pattern that AgentOps already supports. The gap is in the
`_cloud_evaluator_data_mapping` function, which doesn't know how to build
`data_mapping` for these evaluators. Each new evaluator needs:
- An entry in the appropriate frozenset (or a new one)
- The correct `data_mapping` fields (query, response, tool_calls, tool_definitions, etc.)

### Priority 2 — High (improves real-world viability)

| Item | What to do | Effort |
|---|---|---|
| Conversation-format data support | Allow JSONL rows with array-of-messages for query/response fields | Medium — requires dataset format model changes |
| `--no-fail` / `--advisory` flag | Add CLI flag that makes exit code always 0 (report thresholds but don't gate) | Low |
| `config validate` command | Implement the planned command to pre-validate configs in CI | Medium |

### Priority 3 — Medium (documentation)

| Item | What to do | Effort |
|---|---|---|
| Azure DevOps integration pattern | Document how to use `agentops eval run` in an ADO pipeline | Low — docs only |
| Scheduled evaluation pattern | Document cron-triggered eval for drift detection | Low — docs only |
| Advisory mode pattern | Document how to run eval without gating (once `--no-fail` exists) | Low — docs only |
| Multi-environment pattern | Document how to use `project_endpoint_env` across environments | Low — docs only |

### Priority 4 — Future (separate feature)

| Item | What to do | Effort |
|---|---|---|
| Red-team support | New command or new data source type — fundamentally different flow | High — new feature |
| Safety evaluators | `prohibited_actions`, `sensitive_data_leakage`, `violence`, etc. | Medium — requires red-team data source |

---

## 5. Recommendation

**To close issue #51, focus on Priority 1 (missing evaluators).** This is the
single biggest blocker for real-world CI/CD adoption. The evaluators all follow
the same `azure_ai_evaluator` / `builtin.<name>` pattern that AgentOps already
implements — the gap is mechanical, not architectural.

Adding 7 evaluators to `foundry_backend.py` would change the AC 6 verdict from
"NOT MET" to "PARTIALLY MET" (still missing conversation-format data and
red-team, but the core evaluation flow would work for the majority of
field-used evaluators).

Red-team support (Priority 4) should be tracked as a separate issue — it
requires a different data source model (`azure_ai_red_team` with attack
strategies and taxonomy generation) that doesn't fit the current
`agentops eval run` flow.

---

## 6. Summary Scorecard

| Acceptance Criterion | Verdict |
|---|---|
| AC 1: CI/CD integration patterns clearly defined | ⚠️ Partially met |
| AC 2: Pipelines support evaluation as gating mechanism | ✅ Met |
| AC 3: Exit codes correctly interpreted in CI/CD | ✅ Met |
| AC 4: Artifacts generated and usable in pipeline context | ✅ Met |
| AC 5: At least one reference pipeline documented | ✅ Met |
| AC 6: Integration works with real-world scenarios | ❌ Not met |

**Overall: 4/6 met, 1/6 partially met, 1/6 not met.**

The blocking gap is evaluator coverage. AgentOps has the right architecture
for CI/CD integration — declarative config, exit-code gating, artifact
production, generated workflows — but it cannot evaluate the metrics that
real-world Foundry agent pipelines need.
