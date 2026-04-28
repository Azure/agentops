"""WAF-AI Security: diagnostic settings must be configured.

Without diagnostic settings, audit logs and request traces from the
Cognitive Services account never reach a Log Analytics workspace,
storage account, or event hub — making incident investigation and
content-safety auditing effectively impossible.

The WAF-AI Security pillar recommends streaming diagnostic logs to
Log Analytics for every AI account in production.

This rule fires when **none** of the diagnostic settings on the
account ship logs to a destination (workspace / storage / event hub).
"""

from __future__ import annotations

from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.security.diagnostic_settings"


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None:
        return []

    has_destination = any(
        s.workspace_id or s.storage_account_id or s.event_hub_authorization_rule_id
        for s in payload.diagnostic_settings
    )
    has_categories = any(s.enabled_log_categories for s in payload.diagnostic_settings)

    if has_destination and has_categories:
        return []

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.WARNING,
            category=Category.SECURITY,
            title="Diagnostic settings are missing or incomplete",
            summary=(
                f"Cognitive Services account `{account.name}` has "
                f"{len(payload.diagnostic_settings)} diagnostic "
                "setting(s), but none route enabled log categories to "
                "a Log Analytics workspace, storage account, or event "
                "hub. Audit and content-safety logs are not being "
                "captured."
            ),
            recommendation=(
                "Create a diagnostic setting that ships the "
                "`Audit`, `RequestResponse`, and `Trace` log categories "
                "to a Log Analytics workspace. See "
                "https://learn.microsoft.com/azure/ai-services/diagnostic-logging"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "diagnostic_settings": [
                    {
                        "name": s.name,
                        "workspace_id": s.workspace_id,
                        "storage_account_id": s.storage_account_id,
                        "event_hub_authorization_rule_id": s.event_hub_authorization_rule_id,
                        "enabled_log_categories": s.enabled_log_categories,
                    }
                    for s in payload.diagnostic_settings
                ],
            },
        )
    ]
