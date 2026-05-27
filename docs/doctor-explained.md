# The AgentOps Doctor, explained

A 10-minute read for a platform, observability, or AI engineer (and
the engineering managers who own those teams) who runs
`agentops doctor` for the first time. For step-by-step setup, see
the [end-to-end workshop](tutorial-end-to-end.md).

## 1. What the Doctor is - and isn't

The **Doctor** is a regular check-up for an agent project. It reads
signals that are already there (eval history, App Insights telemetry,
Foundry metadata, Azure resource configuration) and emits **findings**
 -  severity-ranked observations with a recommendation attached.

It does *not* fix anything. It does *not* replace Microsoft Foundry's
**Operate → Compliance** surface - Foundry handles guardrails, security
posture, and data governance at the resource level. The Doctor is the
complementary half: runtime telemetry, identity scope, eval discipline,
pipeline hygiene.

A single command:

```
agentops doctor
```

…produces `.agentops/agent/report.md` and a CI-friendly exit code:
`0` = clean, `2` = a finding meets the configured `--severity-fail`
floor, `1` = the analyzer itself errored.

For release reviews, add the evidence flag:

```bash
agentops doctor --evidence-pack
```

That writes `.agentops/release/latest/evidence.json` and `evidence.md`. The
evidence pack summarizes eval, baseline, Doctor, CI/CD workflow, Foundry
continuous-eval, monitoring, AI Landing Zone, and trace-regression readiness
without creating a second exit-code contract.

## 2. The four signal sources

| Source | Reads | Feeds these checks | When it's "ok" |
|---|---|---|---|
| `results_history` | Local `.agentops/results/*/results.json`; Foundry cloud evaluation runs as fallback | `regression`, `latency` (eval), `safety` (eval layer), `opex` (stale + flaky) | At least one local run or a reachable Foundry project with cloud evaluations. |
| `azure_monitor` | App Insights / Log Analytics via KQL | `latency` (p95), `errors` (rate + no-telemetry), `safety` (runtime layer) | Source `enabled: true` + connection reachable. |
| `foundry_control` | Agents, runs, evaluation rules via `azure-ai-projects` | `errors` (Foundry runs), `safety` (continuous-eval rules), `operational_excellence` (Foundry config audit) | `enabled: true` + project endpoint set. |
| `azure_resources` | Cognitive Services account + diagnostic settings via `azure-mgmt-*` | `posture` (WAF-AI Security pillar) | Enabled by default. Doctor uses explicit config first, then AZD `.azure/<env>/.env` when present, then Foundry endpoint/account matching. Reader RBAC is required on the resource group. |

Each source **fails open**: if it's not configured, cannot be inferred, or
its SDK isn't installed, the Doctor reports it as `skipped` in the
diagnostics block with the reason and next setup step. Other checks keep
working.

### Why two sources have "wiring" rules

Two of the four sources, `azure_monitor` and `foundry_control`, are
treated specially: the Doctor also runs a dedicated check on whether
that source is actually wired up.

The reason: dedicated rules fire when a wiring gap exists, so a
project that never even configured App Insights does not show up as
"all clear" simply because there is no production monitoring to grade.

* `errors.no_runtime_telemetry` fires when `azure_monitor` is
  skipped (no `app_insights_resource_id`) or returns an empty
  workspace (zero requests over the lookback window).
* `opex.no_foundry_control_configured` fires when `foundry_control` is
   skipped (no `project_endpoint`) or cannot be read. A reachable Foundry
  project with zero agents is treated as source context, not a finding,
  because the agent may be deployed through HTTP, Container Apps, AKS, or
  another runtime.

Both rules stay silent when the source is explicitly
`enabled: false`. That is how you tell the Doctor "this project does
not use that backend" - the missing backend is treated as a
deliberate opt-out rather than a gap.

### AI Landing Zone deployment readiness

Doctor also reads local AI Landing Zone signals from the workspace: `azure.yaml`,
`manifest.json`, `scripts/Invoke-PreflightChecks.ps1`, generated AgentOps deploy
workflows, and common network-isolation markers. When it sees canonical AI
Landing Zone evidence, it emits an Operational Excellence summary
(`opex.ailz_readiness`) and, if needed, one aggregated warning
(`opex.ailz_gaps`) with the missing readiness dimensions.

The intent is positive and practical: AgentOps helps move the project toward an
AI Landing Zone-ready deployment path by checking that the official preflight,
azd/Bicep workflow, AgentOps eval config, private-network runner plan, and
post-deploy Doctor/eval evidence are wired together.

### Production release readiness

Doctor also emits Operational Excellence findings for the POC-to-production
journey:

- latest eval evidence exists and passed;
- a baseline/comparison exists for regression decisions;
- a trace-regression manifest exists when production traces have been promoted;
- Foundry continuous evaluation is enabled when the control plane is reachable.

These findings feed the optional evidence pack. A `blocked` evidence status means
the release reviewer should stop; `ready_with_warnings` means the release can be
reviewed with explicit gaps. The underlying Doctor exit code still depends only
on `--severity-fail`.

### Extension point: Microsoft 365 Copilot agents

The four sources above all target Azure Foundry workloads.
**Microsoft 365 Copilot agents** (declarative agents shipped as
JSON manifests and custom agents authored in **Copilot Studio**) run
on a separate control plane (Microsoft Graph + Power Platform Admin
APIs + Microsoft 365 Admin Center), so they are not covered today.

The Doctor is designed to grow here without disturbing the existing
contract. A future `microsoft365_agents` source would slot in next
to `foundry_control`, read tenant-scoped agent metadata, and emit
Operational Excellence rules. Candidate auditable signals, all reachable via Graph
+ Power Platform admin APIs without inspecting agent runtime
behaviour:

* `opex.no_m365_agents_configured` - source enabled but
  tenant/environment id not set.
* `opex.no_m365_agents` - source connected but no agents
  registered in the target environment.
* `opex.m365_agent_no_publisher_attestation` - agent has no
  verified publisher / Microsoft Partner Network attestation.
* `opex.m365_agent_no_privacy_url` - agent manifest is missing
  a privacy policy URL (required for tenant-wide distribution).
* `opex.m365_agent_unlabeled` - agent has no sensitivity label
  applied (DLP / Information Protection gap).
* `opex.m365_agent_environment_mismatch` - production agent
  lives in a dev / default Copilot Studio environment instead of a
  managed one.
* `opex.m365_agent_actions_anonymous` - one or more agent
  actions / connectors call out without authentication, bypassing
  tenant DLP.

The first two are workflow-hygiene gaps; the remaining five are
governance signals that fit naturally next to the existing
Operational Excellence rules.

This is a real follow-up, not a quick add: it brings a new dependency
(`msgraph-sdk` or `msal` + raw HTTP), a new auth flow (tenant-level
admin consent), and a larger surface of preview APIs (Power Platform
agent endpoints are still moving). It is intentionally not in the
current release.

## 3. The check families

| Check | Category | Headline question |
|---|---|---|
| `regression` | `quality` | Did any metric drop vs the rolling baseline? |
| `latency` | `performance` | Is p95 latency above the threshold? |
| `errors` | `reliability` | Are production errors / Foundry failures above threshold? *Or* is telemetry connected but silent? |
| `safety` | `responsible_ai` | Three layers: eval content-safety hits, runtime content-filter triggers, missing / disabled continuous-eval rules. |
| `posture` | `security` | WAF-AI Security pillar - local-auth, managed identity, diagnostic settings. |
| `opex_workspace` | `operational_excellence` | Workspace hygiene - pinning, gates, deploy workflows, results gitignore, dataset/bundle versioning, workflow concurrency / SHA pinning, AI Landing Zone deployment readiness. |
| `opex` | `operational_excellence` | Time-based - stale eval runs + flaky-metric drift. |
| `spec_conformance` | `operational_excellence` | Does the implementation match the spec? (spec-kit `.specify/`, `AGENTS.md`, Copilot instructions.) |

## 4. The six categories

| Category | What good looks like |
|---|---|
| `quality` | No regression findings - metrics hold against the rolling baseline. |
| `performance` | Latency p95 inside the threshold both in production and in eval. |
| `reliability` | Error rate under threshold, Foundry runs succeeding, telemetry producing data. |
| `security` | WAF-AI Security pillar findings empty - local-auth disabled, MI configured, diagnostic settings flowing. |
| `responsible_ai` | No content-filter hits in eval or production, continuous evaluation rules attached and enabled. |
| `operational_excellence` | Workspace + CI hygiene clean - versioned datasets / bundles, PR + deploy gates, AI Landing Zone readiness when applicable, no stale evals, no flaky metrics, and the implementation matches the spec. |

## 4b. Spec-conformance rules

When the workspace contains spec-driven-development artifacts
(`.specify/spec.md`, `AGENTS.md`, `.github/copilot-instructions.md`),
the `spec_conformance` check inspects them for drift against the
implementation. Pluggable detectors:

* `spec-kit` - reads `.specify/spec.md`, `plan.md`, `tasks.md`.
* `agents-md` - reads `AGENTS.md`, `.github/copilot-instructions.md`,
  `.github/instructions.md`, `CLAUDE.md`.

Deterministic findings (all `info` / `warning`, never `critical`):

| Finding id | Detection |
|---|---|
| `opex.spec_conformance.spec_missing` | Spec-driven setup detected, but no readable spec body was found; Doctor cannot verify bundles, datasets, tasks, or implementation against intended agent behavior. |
| `opex.spec_conformance.tasks_stale` | Unchecked task-list items in the spec have remained open past `stale_after_days`; Doctor treats this as a signal that the implementation plan may be stale, completed work was not checked off, or the spec was not refreshed after agent behavior changed. |
| `opex.spec_conformance.tasks_orphaned` | Checked task references a file that doesn't exist. |
| `opex.spec_conformance.evaluator_drift` | Spec mentions evaluators absent from `agentops.yaml`. |
| `opex.spec_conformance.dataset_drift` | Spec mentions datasets absent from the workspace. |
| `opex.spec_conformance.agent_drift` | Spec's agent id doesn't match `agentops.yaml`. |

Opt-in LLM gap-analysis
(`opex.spec_conformance.llm.implementation_gap`) runs only when both
the global `checks.llm_assist.enabled` and
`checks.operational_excellence.spec_conformance.llm_assist.enabled`
flags are true (and `AGENTOPS_DOCTOR_LLM_ASSIST` is not `0`). The LLM
rule never emits `critical`. Configure it under:

```yaml
checks:
  operational_excellence:
    spec_conformance:
      enabled: true
      detectors: [spec-kit, agents-md]
      stale_after_days: 30
      skip: []
      llm_assist:
        enabled: false
        severity_floor: 0.6
        max_input_chars: 30000
        max_workspace_paths: 200
```

## 5. A typical report - annotated

```
# AgentOps Doctor Report

## Verdict: ⚠️ Warnings found     ← top-level summary

## Summary
| Severity | Count |              ← scan these first; counts feed CI gating
|---|---|
| 🚨 Critical | 0 |
| ⚠️  Warning  | 3 |
| ℹ️  Info     | 0 |

## Sources
| Source | Status | Detail |     ← which sources actually ran
|---|---|---|
| results_history | ok      | 7 runs loaded
| azure_monitor   | ok      |
| foundry_control | skipped | no project_endpoint configured

## Findings                         ← grouped by category
### Reliability
...
### MLOps / pipeline hygiene
...
```

Each finding has its own detail block with **Severity**, **Category**,
**Source**, and - when the finding matches a row in the WAF knowledge
base - a **WAF** line linking the pillar / area / public Microsoft Learn
page. The detail block also carries the **Recommendation** and an
**Evidence** JSON snippet that's copy-paste-ready for a PR or incident.

## 6. Severities and exit codes

Severities are **independent of category**: a `quality` finding can be
`critical`, `warning`, or `info`. The Doctor's exit codes mirror this:

| Exit code | Meaning |
|---|---|
| `0` | Doctor ran and either found nothing, or nothing at or above the configured `--severity-fail` floor. |
| `2` | Doctor ran and at least one finding is at or above the floor. Treat as a CI failure. |
| `1` | Doctor itself failed (bad config, unreachable source, internal error). |

The default `--severity-fail critical` is good for "fail the PR".
`--severity-fail warning` is good for nightly cron jobs that want to
catch drift before it gets bad.

## 7. LLM-judged checks

Every deterministic check listed above is fast, reproducible, and free
to run in CI. But it leaves a class of signals on the table: anything
that needs semantic judgement of the artefacts the project ships - the
agent's system prompt, the dataset rows, the bundle's evaluator
choice.

The Doctor closes that gap with **LLM-judged checks**. They run on
every `agentops doctor` invocation by default. The judge model is
**auto-discovered from the Foundry project** the first time it runs:
the Doctor lists the project's deployments, picks a chat-capable one
(preferring mini / cheaper models to keep token cost down), caches
the choice, and reuses it on subsequent runs.

### Six advisory rules

| Finding id | Category | What it audits |
|---|---|---|
| `responsible_ai.llm.prompt_transparency` | `responsible_ai` | System prompt discloses AI nature, cites sources, sets a role/scope. |
| `responsible_ai.llm.prompt_safety_guardrails` | `responsible_ai` | System prompt has refusal patterns for the four harm categories (violence, self-harm, sexual, hate / unfairness). |
| `responsible_ai.llm.prompt_jailbreak_surface` | `responsible_ai` | System prompt resists known trapdoor patterns (override phrasing, embedded secrets, unbounded role-play). |
| `responsible_ai.llm.dataset_pii_risk` | `responsible_ai` | Sample of `.agentops/data/*.jsonl` rows scanned for PII (names, emails, phones, ids, addresses, DOBs). |
| `responsible_ai.llm.dataset_bias_signals` | `responsible_ai` | Sample of dataset rows judged for demographic / role / domain / tone / happy-path skew. |
| `opex.llm.bundle_coverage` | `operational_excellence` | Bundle YAML + agent description compared, missing built-in evaluators flagged. |

Findings carry `source: "llm_judge"` and a `[LLM-judged]` prefix in
the title. Severity caps at **WARNING** by design - the judge is
advisory, never fail-the-build. The judge's `confidence` and short
`reasoning` are kept in the finding's evidence so the user can audit
the call.

### Tuning (optional)

```yaml
# .agentops/agent.yaml
checks:
  llm_assist:
    enabled: true            # default; set false to skip the suite
    deployment_name: null    # explicit override; otherwise auto-discovered
    project_endpoint_env: AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
    max_dataset_rows: 50     # cap rows sent to the judge per check
    min_confidence: 0.6      # findings below this are dropped silently
    cache_ttl_days: 30
    rules: []                # empty = run all; or list rule ids to opt-in
```

If you do not want the LLM-judged suite at all - for example, an
ephemeral CI sandbox with no Foundry credentials - set
`enabled: false` and only the deterministic checks run.

### Cost guardrails

* **Auto-discovery prefers mini models.** When picking a deployment
  automatically, the Doctor favours `gpt-*-mini` first so judge calls
  stay cheap by default.
* **Cache.** Each judge call hashes its inputs (prompt, dataset bytes,
  bundle YAML). Results land in `.agentops/cache/llm/<hash>.json`.
  Re-running the Doctor with unchanged inputs costs zero tokens.
* **Sampling.** `max_dataset_rows` caps how many rows the dataset
  rules ship to the judge (default 50).
* **Min confidence.** Low-confidence verdicts are dropped before they
  reach the report, so the only LLM findings you see are ones the
  judge is willing to stand behind.

### Suggested fixes

Every LLM-judged finding asks the judge for **two to four concrete,
case-specific fixes** in addition to its risk verdict. Those land in
the finding's `evidence.suggestions` list and are spliced into the
recommendation block of `report.md`. Cockpit renders them in a
collapsible **Suggested fixes** panel next to each finding. The
panel is read-only by design - the user reviews and applies; the
Doctor itself does not write to files.

## 9. Customising

Three knobs:

```
agentops doctor --categories security,responsible_ai   # only those buckets
agentops doctor --exclude-rules waf.security.diagnostic_settings   # silence one rule
agentops doctor --workspace ./other-project            # point at a different repo
```

For thresholds, source configuration, and check toggles, edit
`.agentops/agent.yaml`. The starter template lives in
`src/agentops/templates/agent.yaml`.

## 10. The WAF knowledge base (editable CSV)

The Doctor ships with a **packaged baseline** at
[`src/agentops/agent/knowledge/waf-checklist.csv`](../src/agentops/agent/knowledge/waf-checklist.csv).
It maps every Doctor finding id to a row that names its WAF pillar,
area, and a public Microsoft Learn reference link. The reporter
annotates each finding with a `WAF: <pillar> / <area>` line when a
match exists.

To **add or override** rules in your own project, edit the workspace
copy at `.agentops/waf-checklist.csv`. `agentops init` scaffolds a
blank version of this file (header + commented examples). The Doctor
reads it on every run and merges with the packaged baseline:

- Rows with a `doctor_check_id` that **already exists** in the
  packaged file **override** that packaged row (pillar, area,
  reference url, etc.).
- Rows with a **new** `doctor_check_id` extend the checklist with
  your own rules.
- Lines starting with `#` are treated as comments.

Strict rule (same as the packaged file): only items the Doctor can
actually *check* belong here. Human-eyeball checklist items are
excluded by design.

The workspace file is meant to be committed to git alongside the
rest of `.agentops/`, so the override is reproducible across team
members and CI.

## 10. Standards we anchor to

- **Microsoft Well-Architected Framework for AI workloads**  -
  https://learn.microsoft.com/azure/well-architected/ai/. Source of
  truth for the *categories* of items (security, reliability,
  performance, operational excellence) and for the WAF pillar /
  area labels in the knowledge base CSV.
- **Microsoft Azure AI Landing Zones Checklist**  -
  https://learn.microsoft.com/azure/cloud-adoption-framework/scenarios/ai/.
  Source of truth for the curated set of Azure-specific checks that
  ship in `.agentops/waf-checklist.csv`. Each Doctor finding cites
  the matching WAF item and links to the Microsoft Learn page.

## 11. Next steps

- Walk through a full setup with Azure resources:
  [end-to-end workshop](tutorial-end-to-end.md).
- Open the workspace command center: `agentops cockpit` shows eval
  history, Doctor findings, CI/CD status, telemetry readiness, and
  Foundry/Azure navigation.
- Audit a repo from CI: there's a ready-made GitHub Actions cron in
  the tutorial.
