"""Azure management-plane source for security posture audits.

Reads the configuration of a Cognitive Services / Azure OpenAI account
and the diagnostic settings attached to it. This is a **read-only**
source intended for the WAF-AI Security pillar checklist.

The source lazy-imports ``azure-mgmt-cognitiveservices`` and
``azure-mgmt-monitor`` so the base CLI does not require the management
SDKs. When the source is disabled, not configured, or the SDK is
missing, returns an empty payload with a diagnostic note (same fail-open
pattern as ``azure_monitor`` and ``foundry_control``).

Required RBAC: ``Reader`` on the resource group (or on each individual
resource), granted to whoever runs ``agentops agent analyze``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agentops.agent.config import AzureResourcesSourceConfig

log = logging.getLogger(__name__)


@dataclass
class CognitiveAccountSnapshot:
    """Subset of cognitive-services account properties relevant to posture."""

    name: str
    location: Optional[str] = None
    sku: Optional[str] = None
    kind: Optional[str] = None
    disable_local_auth: Optional[bool] = None
    public_network_access: Optional[str] = None
    private_endpoint_count: int = 0
    network_acls_default_action: Optional[str] = None
    identity_type: Optional[str] = None
    user_assigned_identities: List[str] = field(default_factory=list)
    custom_subdomain_name: Optional[str] = None


@dataclass
class DeploymentSnapshot:
    name: str
    model: Optional[str] = None
    rai_policy_name: Optional[str] = None


@dataclass
class DiagnosticSettingSnapshot:
    name: str
    workspace_id: Optional[str] = None
    storage_account_id: Optional[str] = None
    event_hub_authorization_rule_id: Optional[str] = None
    enabled_log_categories: List[str] = field(default_factory=list)


@dataclass
class AzureResourcesPayload:
    account: Optional[CognitiveAccountSnapshot] = None
    deployments: List[DeploymentSnapshot] = field(default_factory=list)
    diagnostic_settings: List[DiagnosticSettingSnapshot] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def _resolve_subscription_id(config: AzureResourcesSourceConfig) -> Optional[str]:
    if config.subscription_id:
        return config.subscription_id
    if config.subscription_id_env:
        return os.environ.get(config.subscription_id_env)
    return None


def _network_rule_set_to_default_action(rule_set: Any) -> Optional[str]:
    if rule_set is None:
        return None
    action = getattr(rule_set, "default_action", None)
    if action is None:
        return None
    return getattr(action, "value", str(action))


def _identity_to_snapshot(identity: Any) -> tuple[Optional[str], List[str]]:
    if identity is None:
        return None, []
    type_ = getattr(identity, "type", None)
    type_str = getattr(type_, "value", None) or (str(type_) if type_ else None)
    user_assigned = getattr(identity, "user_assigned_identities", None) or {}
    if isinstance(user_assigned, dict):
        ids = list(user_assigned.keys())
    else:
        ids = []
    return type_str, ids


def collect_azure_resources(
    config: AzureResourcesSourceConfig,
) -> AzureResourcesPayload:
    """Read the cognitive-services account, deployments, and diagnostic settings."""
    diagnostics: Dict[str, Any] = {"enabled": config.enabled}

    if not config.enabled:
        diagnostics["status"] = "disabled"
        return AzureResourcesPayload(diagnostics=diagnostics)

    subscription_id = _resolve_subscription_id(config)
    if not subscription_id or not config.resource_group or not config.cognitive_services_account:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "azure_resources requires subscription_id (or "
            "subscription_id_env), resource_group, and "
            "cognitive_services_account."
        )
        return AzureResourcesPayload(diagnostics=diagnostics)

    diagnostics["target"] = (
        f"/subscriptions/{subscription_id}/resourceGroups/"
        f"{config.resource_group}/providers/Microsoft.CognitiveServices/"
        f"accounts/{config.cognitive_services_account}"
    )

    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
        from azure.mgmt.monitor import MonitorManagementClient
    except ImportError as exc:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "azure-mgmt-cognitiveservices / azure-mgmt-monitor not installed "
            "(install agentops-toolkit[agent])"
        )
        log.info("azure-mgmt-* unavailable: %s", exc)
        return AzureResourcesPayload(diagnostics=diagnostics)

    payload = AzureResourcesPayload(diagnostics=diagnostics)

    try:
        credential = DefaultAzureCredential(exclude_developer_cli_credential=True)
        cs_client = CognitiveServicesManagementClient(credential, subscription_id)
        monitor_client = MonitorManagementClient(credential, subscription_id)

        account = cs_client.accounts.get(
            resource_group_name=config.resource_group,
            account_name=config.cognitive_services_account,
        )
        props = getattr(account, "properties", None)
        sku = getattr(account, "sku", None)
        identity_type, user_assigned = _identity_to_snapshot(
            getattr(account, "identity", None)
        )
        payload.account = CognitiveAccountSnapshot(
            name=config.cognitive_services_account,
            location=getattr(account, "location", None),
            sku=getattr(sku, "name", None) if sku else None,
            kind=getattr(account, "kind", None),
            disable_local_auth=getattr(props, "disable_local_auth", None) if props else None,
            public_network_access=(
                getattr(getattr(props, "public_network_access", None), "value", None)
                or (str(props.public_network_access) if props and props.public_network_access else None)
            ),
            private_endpoint_count=len(
                getattr(props, "private_endpoint_connections", []) or []
            ) if props else 0,
            network_acls_default_action=_network_rule_set_to_default_action(
                getattr(props, "network_acls", None) if props else None
            ),
            identity_type=identity_type,
            user_assigned_identities=user_assigned,
            custom_subdomain_name=getattr(props, "custom_sub_domain_name", None) if props else None,
        )

        # Deployments and content-filter (RAI) policies.
        try:
            deployments = list(
                cs_client.deployments.list(
                    resource_group_name=config.resource_group,
                    account_name=config.cognitive_services_account,
                )
            )
            for d in deployments:
                d_props = getattr(d, "properties", None)
                model = None
                if d_props and getattr(d_props, "model", None):
                    model = getattr(d_props.model, "name", None)
                payload.deployments.append(
                    DeploymentSnapshot(
                        name=getattr(d, "name", "") or "",
                        model=model,
                        rai_policy_name=getattr(d_props, "rai_policy_name", None) if d_props else None,
                    )
                )
        except Exception as exc:  # pragma: no cover - tolerate per-call failures
            diagnostics["deployments_warning"] = str(exc)

        # Diagnostic settings.
        try:
            settings = list(
                monitor_client.diagnostic_settings.list(resource_uri=diagnostics["target"])
            )
            for s in settings:
                logs = getattr(s, "logs", []) or []
                enabled = [
                    getattr(log_, "category", None) or getattr(log_, "category_group", None)
                    for log_ in logs
                    if getattr(log_, "enabled", False)
                ]
                payload.diagnostic_settings.append(
                    DiagnosticSettingSnapshot(
                        name=getattr(s, "name", "") or "",
                        workspace_id=getattr(s, "workspace_id", None),
                        storage_account_id=getattr(s, "storage_account_id", None),
                        event_hub_authorization_rule_id=getattr(
                            s, "event_hub_authorization_rule_id", None
                        ),
                        enabled_log_categories=[c for c in enabled if c],
                    )
                )
        except Exception as exc:  # pragma: no cover
            diagnostics["diagnostic_settings_warning"] = str(exc)

    except Exception as exc:  # pragma: no cover
        diagnostics["status"] = "error"
        diagnostics["reason"] = str(exc)
        log.warning("Azure resources read failed: %s", exc)
        return payload

    diagnostics["status"] = "ok"
    return payload
