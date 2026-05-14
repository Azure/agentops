"""Posture check - runs the WAF-AI rule registry against the resource snapshot."""

from __future__ import annotations

from typing import List

from agentops.agent.checks.posture_rules import RULE_REGISTRY
from agentops.agent.config import PostureCheckConfig
from agentops.agent.findings import Finding
from agentops.agent.sources.azure_resources import AzureResourcesPayload

SOURCE_NAME = "azure_resources"


def run_posture_check(
    resources: AzureResourcesPayload,
    config: PostureCheckConfig,
) -> List[Finding]:
    if not config.enabled:
        return []

    diag = resources.diagnostics or {}
    if diag.get("status") != "ok" or resources.account is None:
        return []

    excluded = {rid.strip() for rid in config.exclude_rules if rid and rid.strip()}

    findings: List[Finding] = []
    for rule_id, rule_fn in RULE_REGISTRY.items():
        if rule_id in excluded:
            continue
        try:
            findings.extend(rule_fn(resources, SOURCE_NAME))
        except Exception:  # pragma: no cover - rules must be defensive
            continue
    return findings
