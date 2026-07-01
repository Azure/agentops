"""Canonical catalog of checks the AgentOps doctor can emit.

The catalog is the single source of truth used by
``agentops doctor explain`` to describe what the analyzer verifies. Each
entry is a static, hand-curated record - the doctor itself does not
use this module at analysis time, so a missing or stale entry only
affects discoverability, not behavior. A drift test
(``tests/unit/test_doctor_catalog.py``) keeps the catalog in step with
the rule registries (`RULE_REGISTRY`, LLM-assist `_ALL_RULES`, and the
deterministic id constants emitted by `run_*` functions).

Categories mirror the Microsoft Well-Architected Framework for AI
pillars, see :class:`agentops.agent.findings.Category`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from agentops.agent.findings import Category, Severity


# ---------------------------------------------------------------------------
# Data source labels
# ---------------------------------------------------------------------------

# Human-readable labels for the "requires" field. Keys must match the
# names used by the analyzer sources / checks.
SOURCE_LABELS: Dict[str, str] = {
    "workspace": "workspace files",
    "results_history": "eval history (local results + Foundry cloud fallback)",
    "azure_monitor": "Azure Monitor / App Insights",
    "foundry_control": "Foundry control plane",
    "azure_resources": "Azure resources (ARM)",
    "spec_workspace": "spec docs (.specify / AGENTS.md)",
    "judge_model": "judge model deployment",
}

SOURCE_DESCRIPTIONS: Dict[str, str] = {
    "workspace": (
        "Local project files: `.agentops/` configs, bundles, datasets, "
        "GitHub Actions / Azure DevOps workflows, AI Landing Zone deployment "
        "signals, `.gitignore`, `CHANGELOG.md`, and other repo files used for "
        "CI / release hygiene checks."
    ),
    "results_history": (
        "Past AgentOps evaluation outputs. Doctor reads local "
        "`.agentops/results/*/results.json` first, then falls back to Foundry "
        "cloud evaluation runs when local history is missing or too short. "
        "Used for metric regressions, stale evaluations, flaky metrics, eval "
        "latency, and content-safety hits from previous runs."
    ),
    "azure_monitor": (
        "Application Insights or Log Analytics telemetry from the deployed "
        "agent. Used for production latency, error rate, rate-limit pressure, "
        "token telemetry, and runtime content-filter signals. Requires "
        "`app_insights_resource_id` or `log_analytics_workspace_id`."
    ),
    "foundry_control": (
        "Foundry project metadata from the control plane: agents, recent run "
        "failures, and continuous-evaluation rules. Uses "
        "`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` or `sources.foundry_control.project_endpoint`."
    ),
    "azure_resources": (
        "Azure ARM resource posture for the Cognitive Services / Azure OpenAI "
        "account: local auth, managed identity, deployments, and diagnostic "
        "settings. This source is enabled by default and fail-open. Doctor "
        "uses explicit `.agentops/agent.yaml` fields when present; otherwise "
        "it reads AZD's `.azure/<env>/.env` when present, then the Foundry "
        "project endpoint from the environment to match the backing Azure AI "
        "account."
    ),
    "spec_workspace": (
        "Spec-driven-development documents such as `.specify/spec.md`, "
        "`.specify/plan.md`, `.specify/tasks.md`, `AGENTS.md`, and Copilot "
        "instructions. Used to check whether the implementation, bundles, "
        "datasets, and tasks still match the intended agent behavior."
    ),
    "judge_model": (
        "A Foundry/OpenAI model deployment used only by opt-in LLM-judged "
        "checks. It reviews semantic signals like prompt guardrails, dataset "
        "PII risk, bundle coverage, and spec-vs-implementation gaps."
    ),
}

# Recognized check flags. Keep this list short and stable.
#
# * ``dynamic_id``  - the id has a dynamic suffix (e.g. metric name)
# * ``llm_judged``  - the check calls a judge LLM (opt-in, costs tokens)
# * ``opt_in``      - the check is off by default and must be enabled
FLAG_LABELS: Dict[str, str] = {
    "dynamic_id": "id varies per metric/signal",
    "llm_judged": "uses a judge model (opt-in)",
    "opt_in": "opt-in (disabled by default)",
}

# Public reference pages shown by `agentops doctor explain`. Exact rule
# links win; category links keep the list useful for newer rules that
# do not yet have a narrower page.
CATEGORY_REFERENCE_URLS: Dict[Category, str] = {
    Category.QUALITY: (
        "https://learn.microsoft.com/azure/ai-foundry/concepts/"
        "evaluation-approach-gen-ai"
    ),
    Category.PERFORMANCE: (
        "https://learn.microsoft.com/azure/well-architected/ai/"
        "performance-efficiency"
    ),
    Category.RELIABILITY: (
        "https://learn.microsoft.com/azure/well-architected/ai/reliability"
    ),
    Category.OPERATIONAL_EXCELLENCE: (
        "https://learn.microsoft.com/azure/well-architected/ai/operations"
    ),
    Category.SECURITY: (
        "https://learn.microsoft.com/azure/well-architected/ai/security"
    ),
    Category.RESPONSIBLE_AI: (
        "https://learn.microsoft.com/azure/well-architected/ai/responsible-ai"
    ),
}

CHECK_REFERENCE_URLS: Dict[str, str] = {
    "regression.<metric>": CATEGORY_REFERENCE_URLS[Category.QUALITY],
    "latency.p95_production": CATEGORY_REFERENCE_URLS[Category.PERFORMANCE],
    "latency.eval_avg": CATEGORY_REFERENCE_URLS[Category.PERFORMANCE],
    "errors.production_rate": CATEGORY_REFERENCE_URLS[Category.RELIABILITY],
    "errors.foundry_runs": CATEGORY_REFERENCE_URLS[Category.RELIABILITY],
    "errors.no_runtime_telemetry": CATEGORY_REFERENCE_URLS[Category.RELIABILITY],
    "safety.<metric>": (
        "https://learn.microsoft.com/azure/ai-foundry/concepts/"
        "evaluation-metrics-built-in"
    ),
    "safety.runtime.<signal>": (
        "https://learn.microsoft.com/azure/ai-foundry/concepts/content-filtering"
    ),
    "safety.config.continuous_eval_missing": (
        "https://learn.microsoft.com/azure/ai-foundry/how-to/online-evaluation"
    ),
    "safety.config.continuous_eval_disabled": (
        "https://learn.microsoft.com/azure/ai-foundry/how-to/online-evaluation"
    ),
    "observability.multiturn_coverage_missing": (
        "https://learn.microsoft.com/azure/foundry/concepts/observability"
    ),
    "observability.trace_sampling_missing": (
        "https://learn.microsoft.com/azure/foundry/concepts/observability"
    ),
    "observability.trace_replay_missing": (
        "https://learn.microsoft.com/azure/foundry/concepts/observability"
    ),
}


# ---------------------------------------------------------------------------
# CheckSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckSpec:
    """A documented doctor check.

    ``id`` is the canonical finding id. When the id has a dynamic
    suffix the spec uses the placeholder form (e.g.
    ``regression.<metric>``) and the ``dynamic_id`` flag is set so the
    UI can render the wildcard explicitly.
    """

    id: str
    category: Category
    title: str
    summary: str
    severities: Tuple[Severity, ...]
    requires: Tuple[str, ...] = field(default_factory=tuple)
    flags: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_dynamic(self) -> bool:
        return "dynamic_id" in self.flags

    @property
    def is_llm_judged(self) -> bool:
        return "llm_judged" in self.flags


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

# Order within each category is informational (most-actionable first);
# the overall WAF pillar order is fixed by `CATEGORY_ORDER` below.
CHECKS: Tuple[CheckSpec, ...] = (
    # ------------------------------------------------------------------
    # Quality
    # ------------------------------------------------------------------
    CheckSpec(
        id="regression.<metric>",
        category=Category.QUALITY,
        title="Metric regression vs rolling baseline",
        summary=(
            "For each metric in the regression watchlist, compare the "
            "latest run to a rolling baseline of previous runs and flag "
            "drops that exceed the configured tolerance."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("results_history",),
        flags=("dynamic_id",),
    ),
    CheckSpec(
        id="observability.multiturn_coverage_missing",
        category=Category.QUALITY,
        title="Multi-turn evaluation coverage is not declared yet",
        summary=(
            "The workspace does not declare multi-turn dataset coverage or "
            "trace-derived conversation rows for Foundry multi-turn evals."
        ),
        severities=(Severity.INFO,),
        requires=("workspace",),
    ),
    # ------------------------------------------------------------------
    # Performance
    # ------------------------------------------------------------------
    CheckSpec(
        id="latency.p95_production",
        category=Category.PERFORMANCE,
        title="Production p95 latency over threshold",
        summary=(
            "App Insights reports a p95 request latency above the "
            "configured ceiling - usually tool-call loops or slow "
            "retrievals leaking into prod."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("azure_monitor",),
    ),
    CheckSpec(
        id="latency.eval_avg",
        category=Category.PERFORMANCE,
        title="Evaluation average latency over threshold",
        summary=(
            "The latest eval run's average per-item latency exceeds "
            "the configured threshold."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("results_history",),
    ),
    # ------------------------------------------------------------------
    # Reliability
    # ------------------------------------------------------------------
    CheckSpec(
        id="errors.production_rate",
        category=Category.RELIABILITY,
        title="Production error rate over threshold",
        summary=(
            "App Insights error rate is above the configured ceiling."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("azure_monitor",),
    ),
    CheckSpec(
        id="errors.foundry_runs",
        category=Category.RELIABILITY,
        title="Foundry run failure rate over threshold",
        summary=(
            "The Foundry control plane reports a run-failure rate "
            "above the configured threshold."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("foundry_control",),
    ),
    CheckSpec(
        id="errors.rate_limit_pressure",
        category=Category.RELIABILITY,
        title="Rate-limit pressure on the model deployment",
        summary=(
            "App Insights shows a non-trivial volume of 429 / "
            "throttling responses against the model endpoint."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("azure_monitor",),
    ),
    CheckSpec(
        id="errors.no_runtime_telemetry",
        category=Category.RELIABILITY,
        title="Runtime telemetry is not configured",
        summary=(
            "The `azure_monitor` source is enabled but no telemetry "
            "was returned - latency / error / token signals will all "
            "be silent until App Insights is wired up."
        ),
        severities=(Severity.WARNING,),
        requires=("azure_monitor",),
    ),
    # ------------------------------------------------------------------
    # Operational Excellence
    # ------------------------------------------------------------------
    CheckSpec(
        id="opex.stale_evaluation",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="No fresh evaluation run in the configured window",
        summary=(
            "The most recent eval is older than the configured "
            "freshness window - measured quality is drifting from the "
            "last validated baseline."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("results_history",),
    ),
    CheckSpec(
        id="opex.flaky_metric.<metric>",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Metric is unstable across recent runs",
        summary=(
            "A metric's coefficient of variation is high enough to "
            "suggest a flaky judge or a prompt that's overly sensitive "
            "to phrasing rather than real agent change."
        ),
        severities=(Severity.WARNING,),
        requires=("results_history",),
        flags=("dynamic_id",),
    ),
    CheckSpec(
        id="opex.no_token_telemetry",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Token usage telemetry is missing",
        summary=(
            "App Insights returned request volume but no token usage "
            "metrics - cost trends and prompt drift can't be tracked."
        ),
        severities=(Severity.WARNING,),
        requires=("azure_monitor",),
    ),
    CheckSpec(
        id="opex.unpinned_agent",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Agent target is not pinned to a version",
        summary=(
            "`agent:` in `agentops.yaml` lacks an explicit version, so "
            "CI silently tracks whatever the Foundry default resolves "
            "to."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.no_thresholds",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="`agentops.yaml` has no explicit thresholds block",
        summary=(
            "Without thresholds the eval gate relies entirely on "
            "auto-defaults - fine for exploration, loose for a merge "
            "gate."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.no_pr_gate",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Repository has no AgentOps PR gate",
        summary=(
            "`.github/workflows/` exists but has no `agentops-pr.yml` "
            "- PRs can merge without running an eval."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.no_deploy_workflow",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Repository has a PR gate but no deploy workflow",
        summary=(
            "There is no `agentops-deploy-*.yml`, so evals are never "
            "re-run against promoted environments (dev / qa / prod)."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.ailz_readiness",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="AI Landing Zone deployment readiness detected",
        summary=(
            "Doctor found canonical AI Landing Zone signals and reports "
            "the local readiness dimensions for landing-zone preflight, "
            "azd/Bicep workflow deployment, AgentOps eval config, and "
            "private-network runner planning."
        ),
        severities=(Severity.INFO,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.ailz_gaps",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="AI Landing Zone deployment readiness has gaps",
        summary=(
            "The workspace looks like an AI Landing Zone project, but "
            "one or more readiness dimensions are missing before CI/CD "
            "can confidently provision and validate the workload."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.release.no_eval_evidence",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="No evaluation evidence is available for release",
        summary=(
            "No completed AgentOps eval run was found, so the project has no "
            "quality evidence to attach to a production promotion."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "results_history"),
    ),
    CheckSpec(
        id="opex.release.latest_eval_failed",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Latest evaluation run failed",
        summary=(
            "The most recent eval result did not pass, making the release "
            "evidence blocked until the failing rows or thresholds are fixed."
        ),
        severities=(Severity.CRITICAL,),
        requires=("results_history",),
    ),
    CheckSpec(
        id="opex.release.no_baseline",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="No baseline result is available for regression gating",
        summary=(
            "The current eval can pass thresholds, but AgentOps has no "
            "known-good baseline or prior run to show whether the candidate "
            "regressed from production behavior."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "results_history"),
    ),
    CheckSpec(
        id="opex.release.no_trace_regression_dataset",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Production traces are not feeding a regression dataset yet",
        summary=(
            "No trace-regression manifest exists, so production incidents and "
            "high-value conversations are not yet part of the eval flywheel."
        ),
        severities=(Severity.INFO,),
        requires=("workspace", "results_history"),
    ),
    CheckSpec(
        id="opex.release.no_continuous_eval",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="No enabled Foundry continuous evaluation rule is attached",
        summary=(
            "The Foundry control plane is reachable, but no enabled online "
            "evaluation rule was detected for ongoing production scoring."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control",),
    ),
    CheckSpec(
        id="observability.trace_sampling_missing",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Intelligent trace sampling is not evidence-ready",
        summary=(
            "The workspace does not declare Foundry trace sampling and the "
            "trace-regression manifest does not include sampling lineage."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="observability.trace_replay_missing",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Trace replay link is not captured in release evidence",
        summary=(
            "The workspace has no trace replay URL in agentops.yaml or in "
            "trace-derived dataset lineage."
        ),
        severities=(Severity.INFO,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.results_not_gitignored",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Eval results are not gitignored",
        summary=(
            "`.agentops/results/` is checked into the repo - large "
            "binary diffs and stale runs will pollute history."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.unversioned_dataset",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Dataset YAML files are missing a `version` field",
        summary=(
            "Datasets without a `version` make run reproducibility "
            "ambiguous when the dataset is edited."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.unversioned_bundle",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Bundle YAML files are missing a `version` field",
        summary=(
            "Bundles without a `version` make evaluator stack changes "
            "invisible across runs."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.results_dir_bloat",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Eval results directory is bloated",
        summary=(
            "`.agentops/results/` has accumulated a large number of "
            "historical runs - prune or archive to keep checkouts fast."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.workflow_concurrency_lock",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="AgentOps workflows are missing a `concurrency:` block",
        summary=(
            "Without concurrency locks, parallel CI runs can race on "
            "the same eval target and produce conflicting telemetry."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.workflow_action_sha_pinning",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="AgentOps workflows pin actions by tag, not by SHA",
        summary=(
            "Tag-pinned actions can change underneath you. Pin to a "
            "commit SHA for supply-chain hardening."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.max_tokens_undefined",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Output token limit is not set on model / evaluator configuration",
        summary=(
            "Missing `max_tokens` / `max_completion_tokens` limits invite "
            "long, expensive responses and unpredictable latency."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace",),
    ),
    CheckSpec(
        id="opex.no_foundry_control_configured",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Foundry control plane is not configured",
        summary=(
            "The `foundry_control` source is enabled but not reachable "
            "- Foundry-side agents, eval rules, and run failures will "
            "stay invisible."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control",),
    ),
    CheckSpec(
        id="opex.spec_conformance.spec_missing",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec setup detected, but no usable specification was found",
        summary=(
            "Doctor found signs that this repo uses spec-driven "
            "development (for example `.specify/`, `AGENTS.md`, or a "
            "`copilot-instructions.md` shell), but could not load a "
            "real spec body. Without that reference, it cannot check "
            "whether bundles, datasets, tasks, and "
            "implementation still match the intended agent behavior."
        ),
        severities=(Severity.WARNING,),
        requires=("spec_workspace",),
    ),
    CheckSpec(
        id="opex.spec_conformance.tasks_stale",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec tasks have been left open past the freshness window",
        summary=(
            "Doctor found unchecked task-list items in the spec "
            "(for example `tasks.md` in a spec-kit workspace) and the "
            "spec has not been updated within the configured freshness "
            "window. This usually means the implementation plan is no "
            "longer trustworthy: either the work is done but the tasks "
            "were not checked off, the tasks are no longer relevant, or "
            "the agent behavior changed without the spec being refreshed."
        ),
        severities=(Severity.INFO, Severity.WARNING),
        requires=("spec_workspace",),
    ),
    CheckSpec(
        id="opex.spec_conformance.tasks_orphaned",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="`tasks.md` references items not present in the spec",
        summary=(
            "Task entries don't map back to anything in the spec - "
            "the plan and the spec are drifting apart."
        ),
        severities=(Severity.WARNING,),
        requires=("spec_workspace",),
    ),
    CheckSpec(
        id="opex.spec_conformance.evaluator_drift",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec lists evaluators that the bundle does not implement",
        summary=(
            "The spec mentions evaluators that are absent from the "
            "AgentOps bundle - real evals don't cover what the spec "
            "promises."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "spec_workspace"),
    ),
    CheckSpec(
        id="opex.spec_conformance.dataset_drift",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec mentions datasets that don't exist in the workspace",
        summary=(
            "Dataset names referenced in the spec are missing from "
            "`.agentops/datasets/`."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "spec_workspace"),
    ),
    CheckSpec(
        id="opex.spec_conformance.agent_drift",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec describes an agent target inconsistent with `agentops.yaml`",
        summary=(
            "The agent name / version in the spec doesn't match the "
            "one pinned in the AgentOps config."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "spec_workspace"),
    ),
    CheckSpec(
        id="opex.spec_conformance.llm.implementation_gap",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="LLM detects spec capabilities missing from the implementation",
        summary=(
            "A judge model compared the spec to the AgentOps workspace "
            "and flagged capabilities the spec promises but the "
            "implementation does not cover."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "spec_workspace", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="opex.spec_conformance.llm.input_too_large",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Spec is too large to evaluate with the judge model",
        summary=(
            "The merged spec exceeded the judge model's input budget "
            "and was skipped or truncated - raise `max_input_chars` "
            "or split the spec."
        ),
        severities=(Severity.INFO,),
        requires=("spec_workspace", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="opex.llm.bundle_coverage",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="LLM-judged evaluator-bundle coverage gap",
        summary=(
            "A judge model reviewed the bundle and flagged risk "
            "dimensions (e.g. safety, groundedness) that no evaluator "
            "currently covers."
        ),
        severities=(Severity.WARNING,),
        requires=("workspace", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    CheckSpec(
        id="waf.security.local_auth_disabled",
        category=Category.SECURITY,
        title="Local (API key) authentication is enabled",
        summary=(
            "The Cognitive Services / Azure OpenAI account still "
            "accepts key-based auth - WAF-AI recommends Entra ID "
            "(managed identity) only."
        ),
        severities=(Severity.CRITICAL,),
        requires=("azure_resources",),
    ),
    CheckSpec(
        id="waf.security.managed_identity",
        category=Category.SECURITY,
        title="Account has no managed identity assigned",
        summary=(
            "Without a managed identity the agent runtime has to fall "
            "back to keys or service principals with broader scopes."
        ),
        severities=(Severity.CRITICAL,),
        requires=("azure_resources",),
    ),
    CheckSpec(
        id="waf.security.diagnostic_settings",
        category=Category.SECURITY,
        title="Diagnostic settings are missing or incomplete",
        summary=(
            "The AI account is not streaming logs / metrics to a Log "
            "Analytics workspace - investigations and audits will be "
            "blind."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("azure_resources",),
    ),
    CheckSpec(
        id="waf.observability.aoai_diagnostic_categories",
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Azure OpenAI usage telemetry categories are not enabled",
        summary=(
            "The Azure OpenAI account is not emitting the RequestResponse "
            "and AzureOpenAIRequestUsage diagnostic log categories to a Log "
            "Analytics workspace, so the Foundry operations dashboard and "
            "any token / latency / throttling analysis render empty."
        ),
        severities=(Severity.WARNING,),
        requires=("azure_resources",),
    ),
    # ------------------------------------------------------------------
    # Responsible AI
    # ------------------------------------------------------------------
    CheckSpec(
        id="safety.<metric>",
        category=Category.RESPONSIBLE_AI,
        title="Content-safety metric tripped in the latest eval",
        summary=(
            "One of the content-safety metrics (violence, self_harm, "
            "sexual, hate_unfairness, protected_material) hit the "
            "configured severity floor on the latest eval run."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("results_history",),
        flags=("dynamic_id",),
    ),
    CheckSpec(
        id="safety.runtime.<signal>",
        category=Category.RESPONSIBLE_AI,
        title="Production content-filter or jailbreak signal observed",
        summary=(
            "App Insights / Log Analytics recorded one or more content "
            "filter or jailbreak triggers within the lookback window."
        ),
        severities=(Severity.WARNING, Severity.CRITICAL),
        requires=("azure_monitor",),
        flags=("dynamic_id",),
    ),
    CheckSpec(
        id="safety.config.continuous_eval_missing",
        category=Category.RESPONSIBLE_AI,
        title="Foundry continuous evaluation is not configured",
        summary=(
            "The Foundry project has no continuous-evaluation rule "
            "wired up - safety regressions in production won't be "
            "caught between manual runs."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control",),
    ),
    CheckSpec(
        id="safety.config.continuous_eval_disabled",
        category=Category.RESPONSIBLE_AI,
        title="Foundry continuous evaluation is configured but disabled",
        summary=(
            "A continuous-evaluation rule exists in Foundry but is "
            "currently turned off."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control",),
    ),
    CheckSpec(
        id="responsible_ai.llm.prompt_transparency",
        category=Category.RESPONSIBLE_AI,
        title="System prompt lacks AI-disclosure / transparency",
        summary=(
            "A judge model reviewed the agent's system prompt and "
            "flagged missing user-facing AI disclosure or transparency "
            "language."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="responsible_ai.llm.prompt_safety_guardrails",
        category=Category.RESPONSIBLE_AI,
        title="System prompt is missing safety guardrails",
        summary=(
            "A judge model flagged the system prompt as lacking "
            "explicit guardrails around harmful / disallowed content."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="responsible_ai.llm.prompt_jailbreak_surface",
        category=Category.RESPONSIBLE_AI,
        title="System prompt has an unusually large jailbreak surface",
        summary=(
            "A judge model evaluated the prompt's resistance to "
            "common jailbreak vectors and surfaced a high-risk "
            "pattern."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="responsible_ai.llm.dataset_pii_risk",
        category=Category.RESPONSIBLE_AI,
        title="Dataset contains likely PII",
        summary=(
            "A judge model scanned the eval dataset for personally "
            "identifiable information and flagged samples that should "
            "be redacted or synthesized."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
    CheckSpec(
        id="responsible_ai.llm.dataset_bias_signals",
        category=Category.RESPONSIBLE_AI,
        title="Dataset shows demographic / topical bias signals",
        summary=(
            "A judge model identified imbalanced coverage of "
            "demographic or topical groups in the eval dataset."
        ),
        severities=(Severity.WARNING,),
        requires=("foundry_control", "judge_model"),
        flags=("llm_judged", "opt_in"),
    ),
)


# Pillar display order. Keep this aligned with `findings.Category`.
CATEGORY_ORDER: Tuple[Category, ...] = (
    Category.QUALITY,
    Category.PERFORMANCE,
    Category.RELIABILITY,
    Category.OPERATIONAL_EXCELLENCE,
    Category.SECURITY,
    Category.RESPONSIBLE_AI,
)


# Human-readable category descriptions used by the list view header.
CATEGORY_DESCRIPTIONS: Dict[Category, str] = {
    Category.QUALITY: (
        "eval-driven signals (regression watchlist)"
    ),
    Category.PERFORMANCE: (
        "latency and throughput signals from eval and production"
    ),
    Category.RELIABILITY: (
        "error, failure, and rate-limit signals"
    ),
    Category.OPERATIONAL_EXCELLENCE: (
        "workspace hygiene, CI gates, spec / config drift, Foundry audit"
    ),
    Category.SECURITY: (
        "identity, auth and diagnostics posture (WAF-AI security pillar)"
    ),
    Category.RESPONSIBLE_AI: (
        "content safety, prompt and dataset RAI heuristics"
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def all_checks() -> Tuple[CheckSpec, ...]:
    """Return the full catalog as an immutable tuple."""
    return CHECKS


def by_category(
    checks: Iterable[CheckSpec] = CHECKS,
) -> Dict[Category, List[CheckSpec]]:
    """Group ``checks`` by category, preserving :data:`CATEGORY_ORDER`."""
    grouped: Dict[Category, List[CheckSpec]] = {c: [] for c in CATEGORY_ORDER}
    for spec in checks:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped


def filter_checks(
    *,
    category: Category | None = None,
    source: str | None = None,
) -> List[CheckSpec]:
    """Return the catalog filtered by category and/or required source."""
    out: List[CheckSpec] = []
    for spec in CHECKS:
        if category is not None and spec.category != category:
            continue
        if source is not None and source not in spec.requires:
            continue
        out.append(spec)
    return out


def reference_url_for(spec: CheckSpec) -> str | None:
    """Return the best public documentation URL for ``spec``.

    The catalog prefers a rule-specific reference when one exists, and
    otherwise falls back to the public WAF-AI pillar page. Returning a
    URL from here means the CLI can display a clickable "learn more"
    line without hardcoding doc links in the presentation layer.
    """
    return CHECK_REFERENCE_URLS.get(spec.id) or CATEGORY_REFERENCE_URLS.get(
        spec.category
    )
