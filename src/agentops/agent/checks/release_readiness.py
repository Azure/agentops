"""Production-readiness checks for the POC-to-production journey."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.foundry_control import FoundryControlPayload
from agentops.agent.sources.results_history import ResultsHistory

SOURCE_NAME = "release_readiness"


def run_release_readiness_check(
    workspace: Path,
    history: ResultsHistory,
    foundry: Optional[FoundryControlPayload],
) -> List[Finding]:
    """Return findings that block or weaken production release evidence."""

    if not _is_agentops_workspace(workspace, history):
        return []

    findings: List[Finding] = []
    findings.extend(_check_latest_eval(history))
    findings.extend(_check_baseline(workspace, history))
    findings.extend(_check_trace_regression_dataset(workspace, history))
    findings.extend(_check_foundry_online_evaluation(foundry))
    return findings


def _is_agentops_workspace(workspace: Path, history: ResultsHistory) -> bool:
    return (
        (workspace / "agentops.yaml").exists()
        or bool(history.runs)
        or (workspace / ".github" / "workflows" / "agentops-pr.yml").exists()
        or (workspace / ".azuredevops" / "pipelines" / "agentops-pr.yml").exists()
    )


def _check_latest_eval(history: ResultsHistory) -> List[Finding]:
    if not history.runs:
        return [
            Finding(
                id="opex.release.no_eval_evidence",
                severity=Severity.WARNING,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="No evaluation evidence is available for release",
                summary=(
                    "AgentOps could not find a completed evaluation run in "
                    "`.agentops/results/` or Foundry fallback history. A "
                    "production promotion should have at least one recent eval "
                    "result attached to the release evidence."
                ),
                recommendation=(
                    "Run `agentops eval analyze`, fix any setup gaps, then run "
                    "`agentops eval run` before promoting the agent."
                ),
                source=SOURCE_NAME,
            )
        ]

    latest = history.runs[-1]
    if latest.run_pass is False:
        return [
            Finding(
                id="opex.release.latest_eval_failed",
                severity=Severity.CRITICAL,
                category=Category.OPERATIONAL_EXCELLENCE,
                title="Latest evaluation run failed",
                summary=(
                    f"The latest eval run `{latest.run_id}` did not pass. "
                    "A release with a failing quality gate should not be "
                    "promoted to production."
                ),
                recommendation=(
                    "Open the latest `report.md` or Foundry evaluation report, "
                    "fix the failing rows or thresholds, and re-run the eval "
                    "before generating release evidence again."
                ),
                source=SOURCE_NAME,
                evidence={"run_id": latest.run_id, "run_pass": latest.run_pass},
            )
        ]
    return []


def _check_baseline(workspace: Path, history: ResultsHistory) -> List[Finding]:
    if not history.runs:
        return []
    baseline = workspace / ".agentops" / "baseline" / "results.json"
    if baseline.exists() or len(history.runs) >= 2:
        return []
    return [
        Finding(
            id="opex.release.no_baseline",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="No baseline result is available for regression gating",
            summary=(
                "AgentOps found an eval run, but no baseline or prior run to "
                "compare against. The gate can say whether thresholds passed, "
                "but not whether the candidate regressed from the last known "
                "good behavior."
            ),
            recommendation=(
                "After a known-good run, copy "
                "`.agentops/results/latest/results.json` to "
                "`.agentops/baseline/results.json` or keep historical runs so "
                "`agentops eval run --baseline` can render deltas."
            ),
            source=SOURCE_NAME,
        )
    ]


def _check_trace_regression_dataset(workspace: Path, history: ResultsHistory) -> List[Finding]:
    if not history.runs:
        return []
    manifest = workspace / ".agentops" / "data" / "trace-regression-manifest.json"
    if manifest.exists():
        return []
    return [
        Finding(
            id="opex.release.no_trace_regression_dataset",
            severity=Severity.INFO,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Production traces are not feeding a regression dataset yet",
            summary=(
                "No trace-regression manifest was found under `.agentops/data/`. "
                "This is acceptable for early exploration, but production "
                "incidents and high-value conversations should become reviewed "
                "regression rows over time."
            ),
            recommendation=(
                "Export relevant App Insights / Foundry traces and run "
                "`agentops eval promote-traces --source <traces.jsonl> --apply` "
                "to create a reviewed production-derived regression dataset."
            ),
            source=SOURCE_NAME,
            evidence={"manifest": str(manifest)},
        )
    ]


def _check_foundry_online_evaluation(
    foundry: Optional[FoundryControlPayload],
) -> List[Finding]:
    if foundry is None:
        return []
    diag = foundry.diagnostics or {}
    if diag.get("status") != "ok":
        return []
    if "evaluation_rules_count" not in diag and "evaluation_rules_warning" not in diag:
        return []
    enabled = [rule for rule in foundry.evaluation_rules if rule.enabled is not False]
    if enabled:
        return []
    return [
        Finding(
            id="opex.release.no_continuous_eval",
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="No enabled Foundry continuous evaluation rule is attached",
            summary=(
                "The Foundry control plane was reachable, but AgentOps did not "
                "detect an enabled continuous evaluation rule. Production "
                "responses may not be sampled and scored after deployment."
            ),
            recommendation=(
                "Enable Foundry continuous evaluation for the production agent "
                "and include at least one safety or quality evaluator so runtime "
                "traffic keeps producing quality evidence."
            ),
            source=SOURCE_NAME,
            evidence={
                "evaluation_rules_count": len(foundry.evaluation_rules),
                "agents": [agent.agent_id for agent in foundry.agents],
            },
        )
    ]
