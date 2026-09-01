"""Governance artifact readiness checks for ASSERT, ACS, and red-team evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from agentops.agent.findings import Category, Finding, Severity
from agentops.core.governance import (
    GovernanceArtifactSummary,
    REDTEAM_STATE_READY,
    REDTEAM_STATE_THRESHOLD_BREACH,
    summarize_acs,
    summarize_assert,
    summarize_redteam,
    summarize_redteam_readiness,
)
from agentops.utils.yaml import load_yaml

SOURCE_NAME = "governance"

_LEGACY_AGENT_PLACEHOLDER = "my-agent:1"


def run_governance_check(workspace: Path) -> List[Finding]:
    """Validate configured governance artifacts without executing external tools."""

    config = _safe_config(workspace)
    summaries = [
        summarize_assert(workspace, config.get("assert_path")),
        summarize_acs(workspace, config.get("acs_path")),
        summarize_redteam(workspace, config.get("redteam_path")),
    ]
    findings: List[Finding] = []
    for summary in summaries:
        finding = _finding_for(summary)
        if finding is not None:
            findings.append(finding)
    redteam_finding = _redteam_readiness_finding(workspace, config)
    if redteam_finding is not None:
        findings.append(redteam_finding)
    return findings


def _redteam_readiness_finding(workspace: Path, config: dict[str, Any]) -> Finding | None:
    """Turn red-team scan-evidence readiness into a Doctor finding.

    Not applicable (no finding) in project-observability-only mode. A threshold
    breach is a critical finding; every other non-ready state is a warning.
    """
    if not _agent_configured(config):
        return None
    readiness = summarize_redteam_readiness(workspace, config)
    if readiness.state == REDTEAM_STATE_READY:
        return None
    severity = (
        Severity.CRITICAL
        if readiness.state == REDTEAM_STATE_THRESHOLD_BREACH
        else Severity.WARNING
    )
    return Finding(
        id=f"governance.redteam_{readiness.state}",
        severity=severity,
        category=Category.SECURITY,
        title=f"Red-team readiness: {readiness.state}",
        summary=readiness.message,
        recommendation=readiness.remediation or (
            "Run `agentops redteam run` to regenerate normalized scan evidence."
        ),
        source=SOURCE_NAME,
        evidence=readiness.to_dict(),
    )


def _agent_configured(config: dict[str, Any]) -> bool:
    value = config.get("agent") if isinstance(config, dict) else None
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and text != _LEGACY_AGENT_PLACEHOLDER


def _finding_for(summary: GovernanceArtifactSummary) -> Finding | None:
    if summary.status == "not_configured":
        return None
    if summary.status == "present":
        return None
    if summary.kind == "acs" and summary.status == "partial":
        return Finding(
            id="governance.acs_partial",
            severity=Severity.WARNING,
            category=Category.SECURITY,
            title="ACS contract is missing checkpoint coverage",
            summary=(
                "An Agent Control Specification contract was found, but it does "
                "not cover every canonical checkpoint: input, LLM, state, tool, output."
            ),
            recommendation=(
                "Review the ACS contract and add controls for the missing checkpoints "
                "or document why the checkpoint is intentionally out of scope."
            ),
            source=SOURCE_NAME,
            evidence=summary.to_dict(),
        )
    if summary.status in {"missing", "invalid"}:
        return Finding(
            id=f"governance.{summary.kind}_{summary.status}",
            severity=Severity.WARNING,
            category=Category.SECURITY,
            title=f"{summary.kind.upper()} governance artifact is {summary.status}",
            summary=summary.message or f"Configured {summary.kind} artifact could not be used.",
            recommendation=(
                "Fix the configured path or remove it from agentops.yaml. "
                "AgentOps only references governance artifacts; it does not execute "
                "ASSERT, apply ACS, or run red-team campaigns."
            ),
            source=SOURCE_NAME,
            evidence=summary.to_dict(),
        )
    return None


def _safe_config(workspace: Path) -> dict[str, Any]:
    path = workspace / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        data = load_yaml(path)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
