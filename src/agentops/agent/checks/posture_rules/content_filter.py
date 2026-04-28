"""WAF-AI Security: every model deployment needs a content filter (RAI policy).

The WAF-AI Security pillar (Responsible AI subsection) requires that
each Azure OpenAI / AI Foundry model deployment have a content filter
applied. The default ``Microsoft.Default`` policy is acceptable; a
deployment with no policy at all is not.

This rule fires for **each** deployment that has no
``rai_policy_name``.
"""

from __future__ import annotations

from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.security.content_filter"


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None or not payload.deployments:
        return []

    missing = [d for d in payload.deployments if not d.rai_policy_name]
    if not missing:
        return []

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            title="One or more deployments have no content filter applied",
            summary=(
                f"{len(missing)} of {len(payload.deployments)} "
                f"deployment(s) on account `{account.name}` have no "
                "RAI / content-filter policy. The WAF-AI Security "
                "pillar requires Responsible AI policies on every "
                "model deployment."
            ),
            recommendation=(
                "Apply a content-filter policy (start with "
                "`Microsoft.Default`, then tune severity thresholds "
                "for your workload) to every deployment listed below. "
                "See https://learn.microsoft.com/azure/ai-services/openai/concepts/content-filter"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "deployments_missing_filter": [
                    {"name": d.name, "model": d.model} for d in missing
                ],
                "deployments_total": len(payload.deployments),
            },
        )
    ]
