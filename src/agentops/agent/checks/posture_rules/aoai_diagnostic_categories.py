"""WAF-AI Operational Excellence: Azure OpenAI usage telemetry must flow.

The Foundry operations dashboard (``agentops telemetry dashboard``) and any
token / latency / throttling analysis depend on two diagnostic log categories
being enabled on the Azure OpenAI (Cognitive Services) account and routed to a
Log Analytics workspace:

* ``RequestResponse`` - per-request traces (status codes, streaming).
* ``AzureOpenAIRequestUsage`` - prompt / generated token counts.

When either category is missing the workbook tiles render empty. This rule
fires when the account does not emit both categories and prints the exact
``az monitor diagnostic-settings create`` command to fix it. The check is
read-only; it never changes Azure.
"""

from __future__ import annotations

import json
from typing import List

from agentops.agent.findings import Category, Finding, Severity
from agentops.agent.sources.azure_resources import AzureResourcesPayload

RULE_ID = "waf.observability.aoai_diagnostic_categories"

REQUIRED_CATEGORIES = ("RequestResponse", "AzureOpenAIRequestUsage")


def _fix_command(account_id: str, workspace_id: str) -> str:
    logs = json.dumps([{"category": c, "enabled": True} for c in REQUIRED_CATEGORIES])
    return (
        "az monitor diagnostic-settings create "
        "--name agentops-foundry-ops "
        f"--resource {account_id} "
        f"--workspace {workspace_id} "
        f"--logs '{logs}'"
    )


def evaluate(payload: AzureResourcesPayload, source_name: str) -> List[Finding]:
    account = payload.account
    if account is None:
        return []

    enabled: set[str] = set()
    for setting in payload.diagnostic_settings:
        for category in setting.enabled_log_categories:
            enabled.add(str(category))

    missing = [c for c in REQUIRED_CATEGORIES if c not in enabled]
    if not missing:
        return []

    account_id = getattr(account, "id", None) or f"<{account.name}-resource-id>"
    workspace_id = next(
        (s.workspace_id for s in payload.diagnostic_settings if s.workspace_id),
        "<log-analytics-workspace-id>",
    )

    return [
        Finding(
            id=RULE_ID,
            severity=Severity.WARNING,
            category=Category.OPERATIONAL_EXCELLENCE,
            title="Azure OpenAI usage telemetry categories are not enabled",
            summary=(
                f"Azure OpenAI account `{account.name}` is not emitting the "
                f"`{'`, `'.join(missing)}` diagnostic log "
                f"{'category' if len(missing) == 1 else 'categories'}. The "
                "Foundry operations dashboard and any token, latency, or "
                "throttling analysis need both `RequestResponse` and "
                "`AzureOpenAIRequestUsage` streamed to a Log Analytics "
                "workspace, so those tiles will render empty."
            ),
            recommendation=(
                "Enable the missing categories with:\n"
                f"{_fix_command(account_id, workspace_id)}"
            ),
            source=source_name,
            evidence={
                "account": account.name,
                "required_categories": list(REQUIRED_CATEGORIES),
                "missing_categories": missing,
                "enabled_categories": sorted(enabled),
            },
        )
    ]
