"""WAF-AI Security: restrict public network access to the AI account.

Cognitive Services / Azure OpenAI accounts default to ``Enabled``
public network access for convenience. For production AI workloads the
WAF-AI Security pillar recommends restricting network access via
private endpoints or a strict network ACL.

This rule fires unless ONE of the following is true:

* ``publicNetworkAccess == 'Disabled'``
* At least one private endpoint connection is attached
* Network ACLs default action is ``Deny``
"""

from __future__ import annotations

from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.security.public_network_access"


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None:
        return []

    pna = (account.public_network_access or "").lower()
    has_private_endpoint = account.private_endpoint_count > 0
    acl_default = (account.network_acls_default_action or "").lower()

    if (
        pna == "disabled"
        or has_private_endpoint
        or acl_default == "deny"
    ):
        return []

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.WARNING,
            category=Category.SECURITY,
            title="Public network access is open and unrestricted",
            summary=(
                f"Cognitive Services account `{account.name}` allows "
                "public network access without a deny-by-default ACL or "
                "a private endpoint. The WAF-AI Security pillar "
                "recommends restricting network access for production "
                "AI workloads."
            ),
            recommendation=(
                "Either set `publicNetworkAccess: Disabled` and attach "
                "a private endpoint, or configure network ACLs with "
                "`defaultAction: Deny` and an explicit allow list. See "
                "https://learn.microsoft.com/azure/ai-services/cognitive-services-virtual-networks"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "public_network_access": account.public_network_access,
                "private_endpoint_count": account.private_endpoint_count,
                "network_acls_default_action": account.network_acls_default_action,
            },
        )
    ]
