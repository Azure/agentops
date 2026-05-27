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
resource), granted to whoever runs ``agentops doctor``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from agentops.agent.config import AzureResourcesSourceConfig

log = logging.getLogger(__name__)

_ACCOUNT_NAME_ENV_KEYS = (
    "AZURE_OPENAI_RESOURCE",
    "AZURE_OPENAI_RESOURCE_NAME",
    "AZURE_AI_SERVICES_RESOURCE",
    "AZURE_AI_SERVICES_RESOURCE_NAME",
    "AZURE_AI_SERVICES_NAME",
    "AZURE_AI_FOUNDRY_RESOURCE_NAME",
    "AZURE_COGNITIVE_SERVICES_ACCOUNT",
    "AZURE_COGNITIVE_SERVICES_ACCOUNT_NAME",
    "SERVICE_API_RESOURCE_NAME",
)

_PROJECT_ENDPOINT_ENV_KEYS = (
    "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "AZURE_AI_PROJECT_ENDPOINT",
)


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


@dataclass
class _ResolvedTarget:
    subscription_id: Optional[str] = None
    resource_group: Optional[str] = None
    account_name: Optional[str] = None
    project_endpoint: Optional[str] = None
    azd_env: Optional[str] = None
    discovery: Dict[str, Any] = field(default_factory=dict)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse the small `.env` dialect emitted by AZD."""
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            parts = shlex.split(raw_value, posix=True)
            value = parts[0] if parts else ""
        except ValueError:
            value = _strip_quotes(raw_value)
        values[key] = value
    return values


def _discover_azd_environment(workspace: Optional[Path]) -> tuple[Optional[str], Dict[str, str], Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {"status": "not_found"}
    if workspace is None:
        diagnostics["reason"] = "no workspace path was provided"
        return None, {}, diagnostics

    azure_dir = workspace / ".azure"
    if not azure_dir.is_dir():
        diagnostics["reason"] = "workspace has no .azure directory"
        return None, {}, diagnostics

    env_name = os.environ.get("AZURE_ENV_NAME")
    config_path = azure_dir / "config.json"
    if not env_name and config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                env_name = raw.get("defaultEnvironment")
        except (json.JSONDecodeError, OSError):
            diagnostics["config_warning"] = "could not read .azure/config.json"

    if not env_name:
        candidates = [
            p.name for p in azure_dir.iterdir() if p.is_dir() and (p / ".env").is_file()
        ]
        if len(candidates) == 1:
            env_name = candidates[0]
        elif candidates:
            diagnostics["status"] = "ambiguous"
            diagnostics["candidates"] = candidates
            diagnostics["reason"] = (
                "multiple AZD environments found; set AZURE_ENV_NAME or "
                ".azure/config.json defaultEnvironment"
            )
            return None, {}, diagnostics

    if not env_name:
        diagnostics["reason"] = "no AZD environment selected"
        return None, {}, diagnostics

    env_path = azure_dir / env_name / ".env"
    if not env_path.is_file():
        diagnostics["status"] = "missing_env_file"
        diagnostics["env"] = env_name
        diagnostics["reason"] = f"{env_path} does not exist"
        return env_name, {}, diagnostics

    values = _parse_dotenv(env_path)
    diagnostics["status"] = "ok"
    diagnostics["env"] = env_name
    diagnostics["path"] = str(env_path)
    diagnostics["keys"] = sorted(values.keys())
    return env_name, values, diagnostics


def _first_value(values: Dict[str, str], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return None


def _endpoint_host(endpoint: Optional[str]) -> Optional[str]:
    if not endpoint:
        return None
    parsed = urlparse(endpoint if "://" in endpoint else f"https://{endpoint}")
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    return host.lower() if host else None


def _endpoint_subdomain(host: Optional[str]) -> Optional[str]:
    if not host:
        return None
    return host.split(".", 1)[0].lower()


def _resolve_target(
    config: AzureResourcesSourceConfig,
    *,
    workspace: Optional[Path],
    project_endpoint: Optional[str],
) -> _ResolvedTarget:
    azd_env, azd_values, azd_diag = _discover_azd_environment(workspace)

    discovery: Dict[str, Any] = {"azd": azd_diag}
    subscription_id = config.subscription_id
    discovery["subscription_id"] = "config" if subscription_id else None
    if not subscription_id:
        subscription_id = azd_values.get("AZURE_SUBSCRIPTION_ID")
        discovery["subscription_id"] = "azd" if subscription_id else None
    if not subscription_id and config.subscription_id_env:
        subscription_id = os.environ.get(config.subscription_id_env)
        discovery["subscription_id"] = "env" if subscription_id else None

    resource_group = config.resource_group
    discovery["resource_group"] = "config" if resource_group else None
    if not resource_group:
        resource_group = azd_values.get("AZURE_RESOURCE_GROUP")
        discovery["resource_group"] = "azd" if resource_group else None

    account_name = config.cognitive_services_account
    discovery["account"] = "config" if account_name else None
    if not account_name:
        account_name = _first_value(azd_values, _ACCOUNT_NAME_ENV_KEYS)
        discovery["account"] = "azd" if account_name else None

    azd_project_endpoint = _first_value(azd_values, _PROJECT_ENDPOINT_ENV_KEYS)
    env_project_endpoint = _first_value(os.environ, _PROJECT_ENDPOINT_ENV_KEYS)
    resolved_project_endpoint = (
        project_endpoint or azd_project_endpoint or env_project_endpoint
    )
    if project_endpoint:
        discovery["project_endpoint"] = "argument"
    elif azd_project_endpoint:
        discovery["project_endpoint"] = "azd"
    elif env_project_endpoint:
        discovery["project_endpoint"] = "env"
    else:
        discovery["project_endpoint"] = None

    return _ResolvedTarget(
        subscription_id=subscription_id,
        resource_group=resource_group,
        account_name=account_name,
        project_endpoint=resolved_project_endpoint,
        azd_env=azd_env,
        discovery=discovery,
    )


def _resource_group_from_id(resource_id: Optional[str]) -> Optional[str]:
    if not resource_id:
        return None
    match = re.search(r"/resourceGroups/([^/]+)/", resource_id, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _account_name(raw: Any) -> str:
    return str(getattr(raw, "name", "") or "")


def _account_kind(raw: Any) -> str:
    return str(getattr(raw, "kind", "") or "").lower()


def _account_endpoint_host(raw: Any) -> Optional[str]:
    props = getattr(raw, "properties", None)
    endpoint = getattr(props, "endpoint", None) if props else None
    return _endpoint_host(endpoint)


def _account_subdomain(raw: Any) -> Optional[str]:
    props = getattr(raw, "properties", None)
    subdomain = getattr(props, "custom_sub_domain_name", None) if props else None
    if subdomain:
        return str(subdomain).lower()
    host = _account_endpoint_host(raw)
    return _endpoint_subdomain(host)


def _is_cognitive_candidate(raw: Any) -> bool:
    kind = _account_kind(raw)
    return kind in {"openai", "aiservices", "cognitiveservices", ""}


def _select_account(
    candidates: List[Any],
    *,
    endpoint_host: Optional[str],
) -> tuple[Optional[Any], Dict[str, Any]]:
    candidates = [c for c in candidates if _is_cognitive_candidate(c)]
    diag: Dict[str, Any] = {
        "candidate_count": len(candidates),
        "candidates": [_account_name(c) for c in candidates if _account_name(c)],
    }
    if not candidates:
        diag["status"] = "not_found"
        return None, diag

    endpoint_host = endpoint_host.lower() if endpoint_host else None
    if endpoint_host:
        for candidate in candidates:
            if _account_endpoint_host(candidate) == endpoint_host:
                diag["status"] = "matched"
                diag["endpoint_match"] = "exact"
                return candidate, diag

        subdomain = _endpoint_subdomain(endpoint_host)
        if subdomain:
            matches = [
                c for c in candidates if _account_subdomain(c) == subdomain
            ]
            if len(matches) == 1:
                diag["status"] = "matched"
                diag["endpoint_match"] = "subdomain"
                return matches[0], diag
            if len(matches) > 1:
                diag["status"] = "ambiguous"
                diag["endpoint_match"] = "subdomain"
                diag["matches"] = [_account_name(c) for c in matches]
                return None, diag

    if len(candidates) == 1:
        diag["status"] = "matched"
        diag["endpoint_match"] = "single_candidate"
        return candidates[0], diag

    diag["status"] = "ambiguous"
    diag["endpoint_match"] = "none"
    return None, diag


def _build_clients(credential: Any, subscription_id: str) -> tuple[Any, Any]:
    from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
    from azure.mgmt.monitor import MonitorManagementClient

    return (
        CognitiveServicesManagementClient(credential, subscription_id),
        MonitorManagementClient(credential, subscription_id),
    )


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
    *,
    workspace: Optional[Path] = None,
    project_endpoint: Optional[str] = None,
) -> AzureResourcesPayload:
    """Read the cognitive-services account, deployments, and diagnostic settings."""
    diagnostics: Dict[str, Any] = {"enabled": config.enabled}

    if not config.enabled:
        diagnostics["status"] = "disabled"
        return AzureResourcesPayload(diagnostics=diagnostics)

    resolved = _resolve_target(
        config,
        workspace=workspace,
        project_endpoint=project_endpoint,
    )
    diagnostics["discovery"] = resolved.discovery
    diagnostics["azd_env"] = resolved.azd_env
    diagnostics["project_endpoint"] = resolved.project_endpoint

    subscription_id = resolved.subscription_id
    if not subscription_id:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "Azure resources source could not determine subscription_id "
            "from `.agentops/agent.yaml`, `.azure/<env>/.env`, or "
            f"`{config.subscription_id_env}`. Configure one of those values "
            "so Doctor can inspect the deployed Azure resources."
        )
        return AzureResourcesPayload(diagnostics=diagnostics)

    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        diagnostics["status"] = "skipped"
        diagnostics["reason"] = (
            "azure-identity is not installed (install agentops-toolkit[agent])"
        )
        log.info("azure-identity unavailable: %s", exc)
        return AzureResourcesPayload(diagnostics=diagnostics)

    payload = AzureResourcesPayload(diagnostics=diagnostics)

    try:
        credential = DefaultAzureCredential(process_timeout=30)
        try:
            cs_client, monitor_client = _build_clients(credential, subscription_id)
        except ImportError as exc:
            diagnostics["status"] = "skipped"
            diagnostics["reason"] = (
                "azure-mgmt-cognitiveservices / azure-mgmt-monitor not installed "
                "(install agentops-toolkit[agent])"
            )
            log.info("azure-mgmt-* unavailable: %s", exc)
            return payload

        endpoint_host = _endpoint_host(resolved.project_endpoint)
        account = None
        resource_group = resolved.resource_group
        account_name = resolved.account_name

        if resource_group and account_name:
            account = cs_client.accounts.get(
                resource_group_name=resource_group,
                account_name=account_name,
            )
            diagnostics["discovery"]["account"] = diagnostics["discovery"].get(
                "account"
            ) or "config"
        elif resource_group:
            candidates = list(cs_client.accounts.list_by_resource_group(resource_group))
            account, selection = _select_account(
                candidates,
                endpoint_host=endpoint_host,
            )
            diagnostics["account_selection"] = selection
            if account is None:
                diagnostics["status"] = "skipped"
                diagnostics["reason"] = (
                    "Azure resources source found the resource group but could "
                    "not select a single Cognitive Services / Azure OpenAI "
                    "account. Configure `sources.azure_resources."
                    "cognitive_services_account` in `.agentops/agent.yaml`."
                )
                return payload
            account_name = _account_name(account)
            diagnostics["discovery"]["account"] = "resource_group_scan"
        elif endpoint_host:
            candidates = list(cs_client.accounts.list())
            account, selection = _select_account(
                candidates,
                endpoint_host=endpoint_host,
            )
            diagnostics["account_selection"] = selection
            if account is None:
                diagnostics["status"] = "skipped"
                diagnostics["reason"] = (
                    "Azure resources source could not match the Foundry project "
                    "endpoint to exactly one Cognitive Services / Azure OpenAI "
                    "account in the subscription. Configure "
                    "`resource_group` and `cognitive_services_account`."
                )
                return payload
            account_name = _account_name(account)
            resource_group = _resource_group_from_id(getattr(account, "id", None))
            diagnostics["discovery"]["resource_group"] = "subscription_scan"
            diagnostics["discovery"]["account"] = "subscription_scan"
        else:
            diagnostics["status"] = "skipped"
            diagnostics["reason"] = (
                "Azure resources source could not determine resource_group and "
                "cognitive_services_account from AZD, config, or Foundry "
                "endpoint. Configure `.azure/<env>/.env` via AZD or set "
                "`sources.azure_resources.resource_group` and "
                "`cognitive_services_account`."
            )
            return payload

        if not resource_group or not account_name:
            diagnostics["status"] = "skipped"
            diagnostics["reason"] = (
                "Azure resources source selected an account but could not "
                "derive both resource_group and account name. Configure them "
                "explicitly in `.agentops/agent.yaml`."
            )
            return payload

        diagnostics["resource_group"] = resource_group
        diagnostics["account"] = account_name
        diagnostics["target"] = (
            f"/subscriptions/{subscription_id}/resourceGroups/"
            f"{resource_group}/providers/Microsoft.CognitiveServices/"
            f"accounts/{account_name}"
        )

        props = getattr(account, "properties", None)
        sku = getattr(account, "sku", None)
        identity_type, user_assigned = _identity_to_snapshot(
            getattr(account, "identity", None)
        )
        payload.account = CognitiveAccountSnapshot(
            name=account_name,
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
                    resource_group_name=resource_group,
                    account_name=account_name,
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
