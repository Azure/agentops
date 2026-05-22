"""WAF-AI Security: local (key-based) authentication must be disabled.

Cognitive Services / Azure OpenAI accounts ship with key-based auth
enabled by default. Microsoft Entra ID is the recommended path for
production AI workloads - keys can be exfiltrated, hard to rotate, and
bypass conditional access policies.

WAF-AI Security pillar reference:
https://learn.microsoft.com/azure/ai-services/openai/how-to/managed-identity
"""

from __future__ import annotations

from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.security.local_auth_disabled"


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None:
        return []
    if account.disable_local_auth is True:
        return []

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.CRITICAL,
            category=Category.SECURITY,
            title="Local (API key) authentication is enabled",
            summary=(
                f"Cognitive Services account `{account.name}` has "
                f"`disableLocalAuth={account.disable_local_auth}`. "
                "Key-based authentication is enabled, which contradicts "
                "the WAF-AI Security pillar guidance to use Microsoft "
                "Entra ID exclusively."
            ),
            recommendation=(
                "Set `disableLocalAuth: true` on the account, grant the "
                "agent runtime the `Cognitive Services OpenAI User` "
                "role via managed identity, and rotate any keys that "
                "may have leaked. See "
                "https://learn.microsoft.com/azure/ai-services/openai/how-to/managed-identity"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "disable_local_auth": account.disable_local_auth,
            },
        )
    ]
