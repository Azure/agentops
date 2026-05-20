"""WAF-AI Security: account must have a managed identity assigned.

Cognitive Services / Azure OpenAI accounts call downstream Azure
resources (Storage for fine-tuning data, Key Vault for customer keys,
etc.). The WAF-AI Security pillar recommends using a managed identity
for those calls instead of connection strings or keys.

This rule fires when the account ``identity.type`` is missing or
``None`` - i.e. neither system-assigned nor user-assigned managed
identity is configured.
"""

from __future__ import annotations

from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.security.managed_identity"

_NO_IDENTITY_VALUES = {"", "none", "null"}


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None:
        return []
    type_ = (account.identity_type or "").strip().lower()
    if type_ and type_ not in _NO_IDENTITY_VALUES:
        return []

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.WARNING,
            category=Category.SECURITY,
            title="Account has no managed identity assigned",
            summary=(
                f"Cognitive Services account `{account.name}` has no "
                "managed identity. The WAF-AI Security pillar "
                "recommends assigning a system- or user-assigned MI so "
                "downstream calls (Storage, Key Vault, Search) avoid "
                "connection strings."
            ),
            recommendation=(
                "Enable a system-assigned managed identity (or attach "
                "a user-assigned one) on the account, and grant it the "
                "minimum role it needs on each downstream resource. "
                "See https://learn.microsoft.com/azure/ai-services/authentication"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "identity_type": account.identity_type,
                "user_assigned_identities": account.user_assigned_identities,
            },
        )
    ]
