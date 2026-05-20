# `agentops doctor` — checks reference

This page is the canonical inventory of every check the AgentOps
Doctor runs. Each row tells you the **finding id** that surfaces in
`report.md` / the cockpit, the **WAF pillar** the check belongs to,
the **data source** it reads, and the **detection signal** (the rule
itself, in plain English).

> **Tip:** the same catalog is browsable from the terminal:
>
> ```bash
> agentops doctor explain                    # paged manual: sources + flow
> agentops doctor explain --open             # browser-friendly copy for printing
> agentops doctor explain -f markdown -o doctor.md
> ```
>
> Use `agentops doctor --help` for terse syntax and options. Use
> `agentops doctor explain` for the Linux-style command manual, including
> sources, execution flow, examples, and the check catalog. Add `--open`
> when you want a printable browser view, or `--format markdown --out` when
> you want a shareable document. Use this page for the deeper "how each
> rule detects the signal" reference.

Two conventions across the table:

* **Mechanism = `programmatic`** — the rule is deterministic: an SDK
  call, a KQL query, a regex, a timestamp comparison, or a statistic.
  Fast, reproducible, free to run, and always on.
* **Mechanism = `llm-judged`** — the rule asks a judge model to look
  at semantic signals a regex can't see (jailbreak surface, evaluator
  coverage, etc.). Gated by `checks.llm_assist.enabled`, capped at
  `warning` severity, never affects CI exit codes.

## Data sources

The Doctor reaches Azure through five sources, all configured in
`.agentops/agent.yaml`:

| Source | Reads |
|---|---|
| `workspace_files` | `.agentops/`, `agentops.yaml`, `.github/workflows/`, `CHANGELOG.md`, spec-kit / `AGENTS.md` |
| `results_history` | Local `.agentops/results/*/results.json` first; Foundry cloud evaluation runs as fallback when local history is missing or too short |
| `azure_monitor` | Application Insights / Log Analytics via REST (KQL) |
| `foundry_control` | Foundry project / agents / evaluation rules via `azure-ai-projects` |
| `azure_resources` | Cognitive Services account properties via `azure-mgmt-cognitiveservices`; inferred from explicit config, AZD `.azure/<env>/.env`, or Foundry endpoint/account matching |

The LLM-judged rules additionally use the Foundry project's OpenAI
client (auto-discovered) as the judge model.

Azure sources fail open. When Doctor cannot authenticate, infer an AZD
environment, match a single Azure AI account, or read a resource, it
records a diagnostic with the reason and suggested setup step instead of
stopping the whole run.

## Catalogue

### 🛡️ Security

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `waf.security.local_auth_disabled` | warning | `azure_resources` | programmatic | `account.disable_local_auth == true` |
| `waf.security.managed_identity` | warning | `azure_resources` | programmatic | `account.identity.type in {SystemAssigned, UserAssigned}` |
| `waf.security.public_network_access` | warning | `azure_resources` | programmatic | `account.publicNetworkAccess == Disabled` (or private endpoint present) |
| `waf.security.diagnostic_settings` | warning | `azure_resources` | programmatic | account has ≥1 diagnostic setting with a `workspace_id` |
| `safety.runtime.content_filter` | warning | `azure_monitor` | programmatic | KQL hits on `gen_ai.response.finish_reasons contains content_filter` |
| `responsible_ai.llm.prompt_jailbreak_surface` | info / warning | `foundry_control` | llm-judged | judge model scans system prompt for override-phrasing, embedded secrets, unbounded role-play |

### ⚙️ Operational Excellence

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `opex.unpinned_agent` | warning | `workspace_files` | programmatic | `agentops.yaml` has `agent:` without a `:version` suffix |
| `opex.no_thresholds` | warning | `workspace_files` | programmatic | `agentops.yaml` has no `thresholds:` block |
| `opex.no_pr_gate` | warning | `workspace_files` | programmatic | `.github/workflows/agentops-pr.yml` missing |
| `opex.no_deploy_workflow` | warning | `workspace_files` | programmatic | no `.github/workflows/agentops-deploy-*.yml` |
| `opex.results_not_gitignored` | warning | `workspace_files` | programmatic | `.agentops/results/` not in any reachable `.gitignore` |
| `opex.unversioned_dataset` | warning | `workspace_files` | programmatic | dataset YAMLs lack a top-level `version:` |
| `opex.unversioned_bundle` | warning | `workspace_files` | programmatic | bundle YAMLs lack a top-level `version:` |
| `opex.results_dir_bloat` | warning | `workspace_files` | programmatic | `.agentops/results/` holds > 50 run folders |
| `opex.workflow_concurrency_lock` | warning | `workspace_files` | programmatic | AgentOps workflows missing a top-level `concurrency:` block |
| `opex.workflow_action_sha_pinning` | warning | `workspace_files` | programmatic | `uses:` pinned to tag instead of 40-char SHA |
| `opex.no_foundry_control_configured` | warning | `foundry_control` | programmatic | Foundry control plane unreachable |
| `opex.stale_evaluation` | warning / critical | `results_history` | programmatic | latest run older than `stale_after_days` (critical at 2×) |
| `opex.flaky_metric.<metric>` | warning | `results_history` | programmatic | coefficient of variation across last N runs > `flaky_cv_threshold` |
| `opex.no_token_telemetry` | warning | `azure_monitor` | programmatic | `request_count > 0` but `gen_ai.usage.input_tokens + output_tokens == 0` |
| `opex.max_tokens_undefined` | warning | `workspace_files` | programmatic | no `max_tokens:` declared in any `agentops.yaml` / bundle YAML that configures a model |
| `opex.llm.bundle_coverage` | info / warning | `workspace_files` | llm-judged | judge compares bundle YAML against agent description and flags missing built-ins |
| `opex.spec_conformance.spec_missing` | warning | `workspace_files` | programmatic | spec-driven setup detected (`.specify/`, `AGENTS.md`, or Copilot instructions) but no readable spec body, so Doctor cannot verify bundles / datasets / tasks against intended agent behavior |
| `opex.spec_conformance.tasks_stale` | warning | `workspace_files` | programmatic | unchecked task-list items in the spec have remained open past `stale_after_days`, which suggests the implementation plan may be stale or the task list was not maintained |
| `opex.spec_conformance.tasks_orphaned` | warning | `workspace_files` | programmatic | checked task references a path missing from workspace |
| `opex.spec_conformance.evaluator_drift` | warning | `workspace_files` | programmatic | evaluator mentioned in spec absent from `agentops.yaml` |
| `opex.spec_conformance.dataset_drift` | warning | `workspace_files` | programmatic | dataset mentioned in spec absent from `.agentops/data/` |
| `opex.spec_conformance.agent_drift` | warning | `workspace_files` | programmatic | spec `agent_id` doesn't match `agentops.yaml` |
| `opex.spec_conformance.llm.implementation_gap` | info / warning | `workspace_files` | llm-judged | judge compares spec capabilities to workspace fingerprint |

### 🛟 Reliability

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `errors.production_rate` | warning / critical | `azure_monitor` | programmatic | KQL `errors / requests > rate_threshold` (critical at 2×) |
| `errors.foundry_runs` | warning | `foundry_control` | programmatic | `foundry.failure_rate > rate_threshold` |
| `errors.no_runtime_telemetry` | warning | `azure_monitor` | programmatic | `monitor.status == ok AND request_count == 0` over lookback |
| `errors.rate_limit_pressure` | warning / critical | `azure_monitor` | programmatic | KQL counts dependency calls with `resultCode == 429`; escalates at 2× the floor |

### ⚡ Performance

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `latency` | warning / critical | `azure_monitor` + `results_history` | programmatic | `p95_seconds > p95_threshold_seconds` (critical at 2×) |

### 🎯 Quality

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `regression.<metric>` | warning / critical | `results_history` | programmatic | `latest_metric - baseline_metric > threshold_drop` per metric |

### 🪞 Responsible AI

| Finding id | Severity | Source | Mechanism | Detection |
|---|---|---|---|---|
| `safety` | warning | `results_history` | programmatic | row-level content-safety metric at or above `severity_floor` in the latest eval |
| `safety.config.continuous_eval_missing` | warning | `foundry_control` | programmatic | `foundry.evaluation_rules` empty while agents exist |
| `safety.config.continuous_eval_disabled` | warning | `foundry_control` | programmatic | any `evaluation_rule.enabled == false` |
| `responsible_ai.llm.prompt_transparency` | info / warning | `foundry_control` | llm-judged | judge scans agent instructions for AI-disclosure / source-citation / role-scope |
| `responsible_ai.llm.prompt_safety_guardrails` | info / warning | `foundry_control` | llm-judged | judge looks for refusal patterns across the four harm categories |
| `responsible_ai.llm.dataset_pii_risk` | info / warning | `workspace_files` | llm-judged | judge scans `.agentops/data/*.jsonl` sample for PII |
| `responsible_ai.llm.dataset_bias_signals` | info / warning | `workspace_files` | llm-judged | judge scores dataset sample for demographic / role / domain / tone skew |

## Configuring the checks

Every threshold lives in `.agentops/agent.yaml` under `checks.<rule>`.
For example, to make the regression check more sensitive:

```yaml
checks:
  regression:
    threshold_drop: 0.05      # 5 percentage points instead of the default 10
    min_runs: 5               # need at least 5 runs for a baseline
```

To suppress a specific rule entirely, add it to `checks.<rule>.skip`
(where supported) or use the CLI flag:

```bash
agentops doctor --categories quality,operational_excellence
agentops doctor --exclude-rules waf.security.diagnostic_settings
```

## Roadmap

The following items are on the AI Landing Zones Checklist but **do not
ship a rule today**. They appear here so users know what's coming and
can prioritize:

| AI.X | Pillar | Plan |
|---|---|---|
| AI.10 | Reliability | TPM/RPM quota adequacy via `azure-mgmt-monitor` metrics (`OpenAI/Quota/UtilizationPercentage`) |
| AI.155 | Reliability | Provisioned-throughput utilization via `azureOpenAIProvisionedManagedUtilization` metric |
| AI.158 | Reliability | AI Search replica count via `azure-mgmt-search` (`searchService.replicaCount >= 2`) |
| AI.140 | Performance | Token-consumption benchmark vs configured target |
| AI.25 | Cost | Cost-per-model tracking via Azure Cost Management API |

Each requires a new Azure SDK dependency; we will land them
incrementally as workloads adopt them.
