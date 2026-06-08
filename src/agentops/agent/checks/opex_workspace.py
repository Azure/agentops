"""Workspace hygiene check for the Operational Excellence pillar.

These rules read the eval workspace (``agentops.yaml`` + ``.agentops/``
+ ``.github/workflows/``) and flag operational-excellence gaps that
aren't covered by Foundry's Operate -> Compliance surface. Examples:

* Agent string isn't pinned to a version (``my-agent`` instead of
  ``my-agent:3``).
* ``agentops.yaml`` ships with no ``thresholds:`` block - the gate is
  loose and depends entirely on auto-defaults.
* Repo has no ``agentops-pr.yml`` CI gate.

Findings live under :class:`Category.OPERATIONAL_EXCELLENCE` with the
``opex.*`` id prefix and default to ``warning`` severity unless
explicitly elevated. The companion time-based rules
(``opex.stale_evaluation``, ``opex.flaky_metric``) live in
:mod:`agentops.agent.checks.opex`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from agentops.agent.findings import Category, Finding, Severity

SOURCE_NAME = "opex_workspace"


def run_opex_workspace_check(workspace: Path) -> List[Finding]:
    """Run all MLOps hygiene rules against ``workspace`` and return findings.

    Each rule is independent and defensive: anything it can't read is
    silently skipped so the watchdog stays useful on partial setups.
    """
    findings: List[Finding] = []

    config_path = workspace / "agentops.yaml"
    config_data = _safe_load_yaml(config_path)

    findings.extend(_check_agent_pinning(config_data))
    findings.extend(_check_thresholds_block(config_data))
    findings.extend(_check_pr_gate_workflow(workspace))
    findings.extend(_check_deploy_gate_workflow(workspace))
    findings.extend(_check_results_gitignored(workspace))
    findings.extend(_check_dataset_versioning(workspace))
    findings.extend(_check_bundle_versioning(workspace))
    findings.extend(_check_results_dir_bloat(workspace))
    findings.extend(_check_workflow_concurrency(workspace))
    findings.extend(_check_workflow_sha_pinning(workspace))
    findings.extend(_check_max_tokens_limit(workspace))
    findings.extend(_check_ailz_readiness(workspace))

    return findings


def _check_agent_pinning(config: Optional[dict]) -> List[Finding]:
    """Warn when `agent:` is not pinned to a `:version` (foundry agent)
    or to an explicit URL/model identifier."""
    if not isinstance(config, dict):
        return []
    agent = config.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        return []

    # URL targets and model: targets are inherently pinned.
    if agent.startswith("http://") or agent.startswith("https://"):
        return []
    if agent.lower().startswith("model:"):
        return []

    # For "name:version" - verify the part after ':' is non-empty and
    # not the literal "latest" alias.
    if ":" in agent:
        _, _, version = agent.partition(":")
        version = version.strip().lower()
        if version and version != "latest":
            return []

    return [
        Finding(
            id="opex.unpinned_agent",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Agent target is not pinned to a version",
            summary=(
                f"`agent: {agent}` has no explicit version. CI runs will "
                "track whatever 'latest' resolves to, so a Foundry edit "
                "to the agent can change eval results without a code "
                "change in this repo."
            ),
            recommendation=(
                "Pin the agent to a published version (for example "
                "`agent: my-agent:3`). Bump the suffix deliberately when "
                "you publish a new version in Foundry."
            ),
            source=SOURCE_NAME,
            evidence={"agent": agent},
        )
    ]


def _check_thresholds_block(config: Optional[dict]) -> List[Finding]:
    """Warn when `thresholds:` is absent or empty - auto-defaults are
    fine for exploration but loose for prod gates."""
    if not isinstance(config, dict):
        return []
    thresholds = config.get("thresholds")
    if isinstance(thresholds, dict) and thresholds:
        return []
    return [
        Finding(
            id="opex.no_thresholds",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="agentops.yaml has no explicit thresholds",
            summary=(
                "Without a `thresholds:` block, AgentOps relies entirely "
                "on auto-defaults to decide whether a run passes or "
                "fails. That is fine for exploration but too loose for a "
                "merge gate."
            ),
            recommendation=(
                "Add a `thresholds:` map to `agentops.yaml` listing the "
                "specific metric floors/ceilings your team agrees on "
                "(e.g. `coherence: \">=3\"`, `avg_latency_seconds: "
                "\"<=30\"`)."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_pr_gate_workflow(workspace: Path) -> List[Finding]:
    """Warn when the repo has no `agentops-pr.yml` CI gate."""
    candidate = workspace / ".github" / "workflows" / "agentops-pr.yml"
    if candidate.exists():
        return []
    # If there's no .github/workflows directory at all, the repo may not
    # be a CI-driven project - only warn when there *is* a workflows dir
    # so we don't pester e.g. local-only sandboxes.
    if not (workspace / ".github" / "workflows").is_dir():
        return []
    return [
        Finding(
            id="opex.no_pr_gate",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Repository has no AgentOps PR gate",
            summary=(
                "There is a `.github/workflows/` directory but no "
                "`agentops-pr.yml`. PRs can merge without running an "
                "AgentOps evaluation, so quality regressions slip "
                "through unchecked."
            ),
            recommendation=(
                "Run `agentops workflow generate` to scaffold the PR "
                "gate + deploy templates, commit the result, and require "
                "the AgentOps PR check on your default branch under "
                "Settings -> Branches."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_deploy_gate_workflow(workspace: Path) -> List[Finding]:
    """Warn when the repo has a PR gate but no `agentops-deploy-*.yml`.

    The PR gate alone protects merges; deploy workflows are what
    actually run an eval against the promoted environment (dev / qa /
    prod). Their absence means CI exercises evals on the PR branch but
    never re-verifies after deployment.
    """
    workflows_dir = workspace / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    deploy_files = list(workflows_dir.glob("agentops-deploy-*.yml")) + list(
        workflows_dir.glob("agentops-deploy-*.yaml")
    )
    if deploy_files:
        return []

    # Only complain when there's a PR gate - otherwise the repo isn't
    # opted into AgentOps CI at all and `no_pr_gate` already covers it.
    if not (workflows_dir / "agentops-pr.yml").exists():
        return []

    return [
        Finding(
            id="opex.no_deploy_workflow",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Repository has a PR gate but no deploy workflow",
            summary=(
                "`.github/workflows/` ships `agentops-pr.yml` but no "
                "`agentops-deploy-*.yml`. CI runs evals on PR branches "
                "but never re-validates the agent against its real "
                "(dev / qa / prod) endpoint after deployment."
            ),
            recommendation=(
                "Run `agentops workflow generate` (it scaffolds deploy "
                "workflows for dev, qa, and prod), commit the result, "
                "and wire the matching OIDC federated credentials in "
                "Azure."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_results_gitignored(workspace: Path) -> List[Finding]:
    """Warn when `.agentops/results/` is not in any reachable .gitignore.

    Committing run artefacts is a real footgun: results.json and
    backend_metrics.json can carry verbatim prompts and model outputs,
    which is fine for short-lived evidence but can leak PII when pushed
    to a shared remote. `agentops init` ships a `.agentops/.gitignore`
    pre-populated with `results/`; this rule flags when that has been
    removed or never existed.
    """
    candidates = [
        workspace / ".gitignore",
        workspace / ".agentops" / ".gitignore",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Accept either the workspace-relative or repo-relative
            # spelling. Trailing `/` is optional.
            normalized = stripped.rstrip("/")
            if normalized in {
                ".agentops/results",
                "results",
                "/results",
                ".agentops/results/*",
            }:
                return []

    # Only warn when there *is* a results directory to protect - empty
    # repos don't need the noise.
    if not (workspace / ".agentops" / "results").is_dir():
        return []

    return [
        Finding(
            id="opex.results_not_gitignored",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Eval results are not gitignored",
            summary=(
                "`.agentops/results/` exists but no reachable "
                "`.gitignore` excludes it. Committing run artefacts "
                "(prompts, model outputs, evidence) to git risks "
                "leaking PII the next time the repo is pushed."
            ),
            recommendation=(
                "Add `results/` to `.agentops/.gitignore` (or "
                "`.agentops/results/` to the repo `.gitignore`). "
                "`agentops init` scaffolds this for you on new "
                "workspaces."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_dataset_versioning(workspace: Path) -> List[Finding]:
    """Warn when dataset YAML files lack a top-level ``version`` field.

    Without a version, edits to a dataset silently change the meaning
    of historical eval runs - a quality regression report can be
    invalidated by an out-of-band dataset edit no one noticed.
    """
    datasets_dir = workspace / ".agentops" / "datasets"
    if not datasets_dir.is_dir():
        return []

    unversioned: List[str] = []
    for path in sorted(datasets_dir.glob("*.yaml")):
        data = _safe_load_yaml(path)
        if data is None:
            # Unreadable / non-dict YAML: treat as unversioned so the
            # finding nudges the user to clean it up.
            unversioned.append(path.name)
            continue
        if "version" not in data:
            unversioned.append(path.name)

    if not unversioned:
        return []

    return [
        Finding(
            id="opex.unversioned_dataset",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Dataset YAML files are missing a `version` field",
            summary=(
                f"{len(unversioned)} dataset YAML file(s) in "
                "`.agentops/datasets/` lack a top-level `version:` "
                "field. Edits to the dataset will silently change the "
                "meaning of historical eval runs."
            ),
            recommendation=(
                "Add `version: 1` (or a higher integer when you change "
                "the dataset) to each dataset YAML. Bump the version "
                "whenever you edit the underlying JSONL rows so "
                "regression comparisons remain meaningful."
            ),
            source=SOURCE_NAME,
            evidence={"files": unversioned},
        )
    ]


def _check_bundle_versioning(workspace: Path) -> List[Finding]:
    """Warn when bundle YAML files lack a top-level ``version`` field.

    Bundles encode evaluator + threshold policy. Editing the policy
    without bumping a version invalidates comparisons across historical
    runs in exactly the same way an un-versioned dataset edit does.
    """
    bundles_dir = workspace / ".agentops" / "bundles"
    if not bundles_dir.is_dir():
        return []

    unversioned: List[str] = []
    for path in sorted(bundles_dir.glob("*.yaml")):
        data = _safe_load_yaml(path)
        if data is None:
            unversioned.append(path.name)
            continue
        if "version" not in data:
            unversioned.append(path.name)

    if not unversioned:
        return []

    return [
        Finding(
            id="opex.unversioned_bundle",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Bundle YAML files are missing a `version` field",
            summary=(
                f"{len(unversioned)} bundle YAML file(s) in "
                "`.agentops/bundles/` lack a top-level `version:` "
                "field. Edits to evaluators or thresholds will "
                "silently change the meaning of historical eval runs."
            ),
            recommendation=(
                "Add `version: 1` (or higher) to each bundle YAML. "
                "Bump the version whenever you change evaluators or "
                "thresholds so regression comparisons remain "
                "meaningful."
            ),
            source=SOURCE_NAME,
            evidence={"files": unversioned},
        )
    ]


def _check_results_dir_bloat(workspace: Path) -> List[Finding]:
    """Warn when ``.agentops/results/`` has grown past a healthy size.

    Results directories grow unboundedly by design - every CI run
    writes a new timestamped folder. Past ~50 runs that's just clutter,
    inflates clone times, and makes the cockpit slow. The fix is
    either archival (move old runs to blob storage) or a rotation
    policy in CI.
    """
    results_dir = workspace / ".agentops" / "results"
    if not results_dir.is_dir():
        return []

    run_dirs = [
        d
        for d in results_dir.iterdir()
        if d.is_dir() and d.name != "latest"
    ]
    threshold = 50
    if len(run_dirs) <= threshold:
        return []

    severity = (
        Severity.CRITICAL if len(run_dirs) >= threshold * 4 else Severity.WARNING
    )
    return [
        Finding(
            id="opex.results_dir_bloat",
            severity=severity,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Eval results directory is bloated",
            summary=(
                f"`.agentops/results/` holds {len(run_dirs)} run "
                f"folders (threshold: {threshold}). Past this point "
                "the directory mostly clutters clones, slows the "
                "cockpit, and obscures the runs that actually "
                "matter."
            ),
            recommendation=(
                "Archive old runs (e.g. upload to blob storage) or "
                "add a retention step to CI that prunes runs older "
                "than your chosen window. The `latest/` pointer is "
                "always preserved and does not count toward the "
                "threshold."
            ),
            source=SOURCE_NAME,
            evidence={
                "run_count": len(run_dirs),
                "threshold": threshold,
            },
        )
    ]


def _check_workflow_concurrency(workspace: Path) -> List[Finding]:
    """Warn when AgentOps workflows lack a top-level ``concurrency:`` block.

    Without one, two pushes on the same PR run in parallel and
    double-bill Azure model quota - WAF AI Cost & Quota Management
    asks for the opposite.
    """
    workflows_dir = workspace / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    candidates = list(workflows_dir.glob("agentops-pr.yml")) + list(
        workflows_dir.glob("agentops-deploy-*.yml")
    ) + list(workflows_dir.glob("agentops-deploy-*.yaml"))

    offenders: List[str] = []
    for path in candidates:
        data = _safe_load_yaml(path)
        if not isinstance(data, dict):
            continue
        if "concurrency" not in data:
            offenders.append(path.name)

    if not offenders:
        return []

    return [
        Finding(
            id="opex.workflow_concurrency_lock",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="AgentOps workflows are missing a `concurrency:` block",
            summary=(
                f"{len(offenders)} AgentOps workflow(s) under "
                "`.github/workflows/` have no top-level "
                "`concurrency:` block. Two pushes on the same PR "
                "(or two pipeline runs against the same environment) "
                "will execute in parallel and double-bill Azure "
                "model quota."
            ),
            recommendation=(
                "Add a `concurrency:` block, for example:\n"
                "```yaml\n"
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.ref }}\n"
                "  cancel-in-progress: true\n"
                "```"
            ),
            source=SOURCE_NAME,
            evidence={"files": offenders},
        )
    ]


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_USES = re.compile(r'^\s*-?\s*uses\s*:\s*([^\s#]+)\s*(?:#.*)?$', re.IGNORECASE)


def _check_workflow_sha_pinning(workspace: Path) -> List[Finding]:
    """Warn when AgentOps workflows pin actions by tag instead of SHA.

    WAF AI Reproducible Workflows asks for dependency immutability.
    Tags can move; commit SHAs cannot.
    """
    workflows_dir = workspace / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return []

    offenders: List[Dict[str, Any]] = []
    for path in sorted(workflows_dir.glob("agentops-*.yml")) + sorted(
        workflows_dir.glob("agentops-*.yaml")
    ):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            match = _USES.match(line)
            if not match:
                continue
            ref = match.group(1)
            # Skip local actions and docker:// refs.
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            if "@" not in ref:
                continue
            _, _, suffix = ref.rpartition("@")
            if _SHA40.match(suffix):
                continue
            offenders.append({"file": path.name, "line": line_no, "ref": ref})

    if not offenders:
        return []

    return [
        Finding(
            id="opex.workflow_action_sha_pinning",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="AgentOps workflows pin actions by tag, not by commit SHA",
            summary=(
                f"{len(offenders)} `uses:` line(s) across AgentOps "
                "workflows pin a GitHub Action to a tag (e.g. `@v4`) "
                "rather than a 40-character commit SHA. Tags are "
                "mutable; CI runs are not reproducible if the tag "
                "moves."
            ),
            recommendation=(
                "Replace each `uses: <owner>/<repo>@<tag>` with "
                "`uses: <owner>/<repo>@<40-char-sha>`. The Dependabot "
                "`github-actions` ecosystem can keep these pinned "
                "SHAs current automatically."
            ),
            source=SOURCE_NAME,
            evidence={"offenders": offenders},
        )
    ]


def _safe_load_yaml(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = YAML(typ="safe").load(handle)
    except (OSError, YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _check_max_tokens_limit(workspace: Path) -> List[Finding]:
    """AI.26 — every model deployment / call should set an output token limit.

    Without an upper bound, a runaway prompt or a malicious user can
    drive the bill arbitrarily high. We look in two places:

    * ``agentops.yaml`` at the project root.
    * Every ``*.yaml`` under ``.agentops/bundles/`` (evaluator bundles
      that drive eval-time model calls).

    The check is permissive: it fires only when at least one file
    explicitly looks like it configures a model (has ``model:``,
    ``deployment:``, or an ``evaluators:`` list) **and** none of the
    candidate files declares ``max_tokens`` or ``max_completion_tokens``. That avoids false
    positives on bare workspaces / agent-only configs.
    """
    candidates: List[Path] = []
    root = workspace / "agentops.yaml"
    if root.exists():
        candidates.append(root)
    bundles_dir = workspace / ".agentops" / "bundles"
    if bundles_dir.is_dir():
        candidates.extend(sorted(bundles_dir.glob("*.y*ml")))
    if not candidates:
        return []

    looks_model_shaped = False
    files_with_token_limit: List[str] = []
    files_without_token_limit: List[str] = []

    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Cheap, format-agnostic detection: matches output token limits at
        # any nesting level in any of the candidate YAMLs. Reasoning models
        # require `max_completion_tokens`; older chat models use `max_tokens`.
        if re.search(r"(?m)^\s*(max_tokens|max_completion_tokens)\s*:", text):
            files_with_token_limit.append(str(path.relative_to(workspace)).replace("\\", "/"))
            looks_model_shaped = True
            continue
        # Only count files that actually look like they configure a model.
        if re.search(
            r"(?m)^\s*(model|deployment|evaluators)\s*:",
            text,
        ):
            looks_model_shaped = True
            files_without_token_limit.append(
                str(path.relative_to(workspace)).replace("\\", "/")
            )

    if not looks_model_shaped:
        return []
    if files_with_token_limit and not files_without_token_limit:
        return []
    if not files_without_token_limit:
        return []

    return [
        Finding(
            id="opex.max_tokens_undefined",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Output token limit is not set on model / evaluator configuration",
            summary=(
                "Found model / evaluator YAML files that do not declare "
                "a `max_tokens:` or `max_completion_tokens:` ceiling. Without an upper bound a single "
                "runaway completion or a malicious prompt can drive token "
                "spend arbitrarily high."
            ),
            recommendation=(
                "Add a `max_tokens:` field for chat models, or "
                "`max_completion_tokens:` for reasoning models such as "
                "`gpt-5` and `o` series deployments. Pick a value just above "
                "your longest legitimate response so legitimate traffic isn't truncated."
            ),
            source=SOURCE_NAME,
            evidence={
                "files_without_token_limit": files_without_token_limit[:10],
                "files_with_token_limit": files_with_token_limit[:10],
            },
        )
    ]


def _check_ailz_readiness(workspace: Path) -> List[Finding]:
    """Summarize AI Landing Zone deployment readiness from local files only.

    This check intentionally does not call az, azd, Azure, or Foundry. It
    activates only on canonical AI Landing Zone signals so generic azd projects
    do not receive landing-zone-specific findings.
    """

    signals = _ailz_signals(workspace)
    if not signals:
        return []

    preflight_script = workspace / "scripts" / "Invoke-PreflightChecks.ps1"
    has_preflight_script = preflight_script.exists()
    has_agentops_config = (workspace / "agentops.yaml").exists()
    workflow_files = _agentops_deploy_workflow_files(workspace)
    workflow_text = "\n".join(_safe_read_text(path) for path in workflow_files)
    has_azd_workflow = "azd provision" in workflow_text.lower()
    has_preflight_workflow = "invoke-preflightchecks.ps1" in workflow_text.lower()
    network_isolated = _ailz_network_isolation_detected(workspace)
    runner_path_declared = (
        not network_isolated
        or _private_runner_path_declared(workflow_text)
        or _private_runner_path_declared(_safe_read_text(workspace / "README.md"))
    )
    deployment_mode = _ailz_deployment_mode(workspace)

    dimensions = {
        "canonical_signals": signals,
        "deployment_mode": deployment_mode,
        "ailz_preflight_script": has_preflight_script,
        "agentops_eval_config": has_agentops_config,
        "azd_deploy_workflow": has_azd_workflow,
        "ailz_preflight_in_workflow": has_preflight_workflow,
        "network_isolation_detected": network_isolated,
        "private_runner_path_declared": runner_path_declared,
    }

    findings = [
        Finding(
            id="opex.ailz_readiness",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="AI Landing Zone deployment readiness detected",
            summary=(
                "This workspace has AI Landing Zone deployment signals. "
                "AgentOps can help turn those signals into an actionable "
                "path: landing-zone preflight, azd/Bicep provisioning, "
                "Doctor checks, eval gates, and post-deploy evidence."
            ),
            recommendation=(
                "Run `agentops workflow analyze` to review the deployment "
                "path, then generate azd-based workflows once the readiness "
                "dimensions in the evidence are intentionally set."
            ),
            source=SOURCE_NAME,
            evidence=dimensions,
        )
    ]

    gaps: List[str] = []
    if not has_preflight_script:
        gaps.append("AILZ preflight script is missing from scripts/Invoke-PreflightChecks.ps1")
    if not has_agentops_config:
        gaps.append("agentops.yaml is missing, so post-deploy eval validation is not configured")
    if not has_azd_workflow:
        gaps.append("no AgentOps azd deploy workflow was found")
    elif not has_preflight_workflow and has_preflight_script:
        gaps.append("AgentOps azd deploy workflow does not run the AILZ preflight script")
    if network_isolated and not runner_path_declared:
        gaps.append(
            "network isolation is detected but the CI runner/private-network execution path is not declared"
        )

    if gaps:
        findings.append(
            Finding(
                id="opex.ailz_gaps",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="AI Landing Zone deployment readiness needs attention",
                summary=(
                    "The project has AI Landing Zone signals, but one or "
                    "more readiness dimensions are still missing before the "
                    "pipeline can confidently provision and validate the "
                    "workload."
                ),
                recommendation=(
                    "Address the gaps listed in evidence. A common path is: "
                    "`agentops init`, `agentops eval analyze`, "
                    "`agentops workflow analyze`, then "
                    "`agentops workflow generate --deploy-mode azd --force`. "
                    "For hub-spoke or private-network rollout, use "
                    "`/agentops-workflow` to adapt runner access explicitly."
                ),
                source=SOURCE_NAME,
                evidence={"gaps": gaps, "readiness": dimensions},
            )
        )

    return findings


def _ailz_signals(workspace: Path) -> List[str]:
    signals: List[str] = []

    manifest = _safe_load_json(workspace / "manifest.json")
    if isinstance(manifest, dict) and any(
        key in manifest for key in ("ailz_tag", "ailz_version")
    ):
        signals.append("manifest.json pins AI Landing Zone version")

    azure_yaml = _safe_load_yaml(workspace / "azure.yaml")
    if isinstance(azure_yaml, dict):
        metadata = azure_yaml.get("metadata")
        template = ""
        if isinstance(metadata, dict):
            template = str(metadata.get("template") or "")
        name = str(azure_yaml.get("name") or "")
        if template == "azure-ai-lz" or name == "azure-ai-lz":
            signals.append("azure.yaml identifies the azure-ai-lz template")
        if _azure_yaml_invokes_ailz_preflight(azure_yaml):
            signals.append("azure.yaml wires the AI Landing Zone preprovision hook")

    if (workspace / "scripts" / "Invoke-PreflightChecks.ps1").exists():
        signals.append("scripts/Invoke-PreflightChecks.ps1 is present")

    return sorted(set(signals))


def _safe_load_json(path: Path) -> Optional[dict]:
    text = _safe_read_text(path)
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _safe_read_text(path: Path) -> str:
    try:
        if not path.exists() or path.stat().st_size > 200_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _azure_yaml_invokes_ailz_preflight(data: dict) -> bool:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    return "invoke-preflightchecks.ps1" in str(hooks).lower()


def _ailz_deployment_mode(workspace: Path) -> str:
    params = _safe_load_json(workspace / "main.parameters.json")
    if not isinstance(params, dict):
        params = _safe_load_json(workspace / "infra" / "main.parameters.json")
    if isinstance(params, dict):
        raw = (params.get("parameters") or {}).get("deploymentMode")
        if isinstance(raw, dict) and raw.get("value"):
            return str(raw["value"])
    text = _safe_read_text(workspace / "README.md").lower()
    if "ailz-integrated" in text or "hub-and-spoke" in text:
        return "ailz-integrated"
    if "standalone" in text and "ai landing zone" in text:
        return "standalone"
    return "unknown"


def _ailz_network_isolation_detected(workspace: Path) -> bool:
    candidates = [
        workspace / "main.parameters.json",
        workspace / "infra" / "main.parameters.json",
        workspace / "main.bicep",
        workspace / "infra" / "main.bicep",
        workspace / "README.md",
    ]
    text = "\n".join(_safe_read_text(path) for path in candidates).lower()
    return any(
        term in text
        for term in (
            "network_isolation",
            "networkisolation",
            "privateendpoint",
            "private endpoint",
            "azurefirewall",
            "azure firewall",
            "bastion",
            "jumpbox",
            "acr_task_agent_pool",
            "acr tasks",
            "hubintegration",
        )
    )


def _agentops_deploy_workflow_files(workspace: Path) -> List[Path]:
    candidates: List[Path] = []
    for root in (
        workspace / ".github" / "workflows",
        workspace / ".azuredevops" / "pipelines",
    ):
        if not root.is_dir():
            continue
        candidates.extend(sorted(root.glob("agentops-deploy-*.yml")))
        candidates.extend(sorted(root.glob("agentops-deploy-*.yaml")))
    return candidates


def _private_runner_path_declared(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "self-hosted",
            "private runner",
            "private agent",
            "jumpbox",
            "acr tasks",
            "acr_task",
            "agent pool",
            "agent-pool",
        )
    )
