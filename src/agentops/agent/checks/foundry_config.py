"""Foundry control-plane configuration audit (Operational Excellence category).

Mirrors the ``errors.no_runtime_telemetry`` pattern for the Foundry
control plane. The Doctor warns when Foundry was expected but the
control-plane source is unconfigured or unreachable.

If the user explicitly opted out (``foundry_control.enabled: false``)
we stay silent - that is the documented way to say "we are not on
Foundry".
"""

from __future__ import annotations

from typing import List, Optional

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.foundry_control import FoundryControlPayload

SOURCE_NAME = "foundry_control"


def run_foundry_config_check(
    foundry: Optional[FoundryControlPayload],
) -> List[Finding]:
    """Audit the Foundry control plane configuration."""
    if foundry is None:
        return []

    diag = foundry.diagnostics or {}
    status = diag.get("status")

    if status == "disabled":
        return []

    findings: List[Finding] = []
    if status != "ok":
        findings.append(_no_foundry_control_finding(diag))
        return findings

    return findings


def _no_foundry_control_finding(diag: dict) -> Finding:
    status = diag.get("status") or "unknown"
    reason = diag.get("reason") or (
        "the source is enabled but did not return a healthy status"
    )
    return Finding(
        id="opex.no_foundry_control_configured",
        severity=Severity.WARNING,
        category=Category.OPERATIONAL_EXCELLENCE,
        title="Foundry control plane is not configured",
        summary=(
            "The `foundry_control` source is enabled but reports "
            f"`status: {status}` ({reason}). Without it, Doctor "
            "cannot see Foundry-side agents, evaluation rules, or "
            "run failures, so safety-config and Foundry-run checks "
            "stay grey."
        ),
        recommendation=(
            "Set `sources.foundry_control.project_endpoint` (or the "
            "`AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` env var) in "
            "`.agentops/agent.yaml`, install the `[foundry]` extra, "
            "and grant the running identity at least `Reader` on the "
            "Foundry project. If this project does not use Foundry, "
            "set `sources.foundry_control.enabled: false` to opt out "
            "explicitly."
        ),
        source=SOURCE_NAME,
        evidence={
            "monitor_status": status,
            "reason": reason,
            "mode": "not_configured",
        },
    )
