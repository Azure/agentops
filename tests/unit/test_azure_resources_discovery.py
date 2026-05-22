from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

from agentops.agent.config import AzureResourcesSourceConfig, PostureCheckConfig
from agentops.agent.sources import azure_resources
from agentops.agent.sources.azure_resources import (
    _discover_azd_environment,
    _parse_dotenv,
    _select_account,
    collect_azure_resources,
)


def _account(
    name: str,
    *,
    endpoint: str | None = None,
    kind: str = "OpenAI",
    resource_group: str = "rg-ai",
    custom_subdomain_name: str | None = None,
):
    return SimpleNamespace(
        id=(
            "/subscriptions/sub-1/resourceGroups/"
            f"{resource_group}/providers/Microsoft.CognitiveServices/accounts/{name}"
        ),
        name=name,
        kind=kind,
        location="eastus",
        sku=SimpleNamespace(name="S0"),
        identity=SimpleNamespace(type="SystemAssigned", user_assigned_identities={}),
        properties=SimpleNamespace(
            endpoint=endpoint,
            custom_sub_domain_name=custom_subdomain_name,
            disable_local_auth=True,
            public_network_access=SimpleNamespace(value="Disabled"),
            private_endpoint_connections=[],
            network_acls=None,
        ),
    )


def test_parse_dotenv_handles_azd_style_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "AZURE_SUBSCRIPTION_ID=sub-1",
                "export AZURE_RESOURCE_GROUP='rg-ai'",
                'AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://project.services.ai.azure.com"',
                "IGNORED",
                "INLINE=value # comment",
            ]
        ),
        encoding="utf-8",
    )

    values = _parse_dotenv(env_path)

    assert values["AZURE_SUBSCRIPTION_ID"] == "sub-1"
    assert values["AZURE_RESOURCE_GROUP"] == "rg-ai"
    assert (
        values["AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"]
        == "https://project.services.ai.azure.com"
    )
    assert values["INLINE"] == "value"
    assert "IGNORED" not in values


def test_discover_azd_environment_prefers_azure_env_name(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / ".azure" / "dev"
    second = tmp_path / ".azure" / "prod"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / ".env").write_text("AZURE_RESOURCE_GROUP=rg-dev", encoding="utf-8")
    (second / ".env").write_text("AZURE_RESOURCE_GROUP=rg-prod", encoding="utf-8")
    monkeypatch.setenv("AZURE_ENV_NAME", "prod")

    env_name, values, diagnostics = _discover_azd_environment(tmp_path)

    assert env_name == "prod"
    assert values["AZURE_RESOURCE_GROUP"] == "rg-prod"
    assert diagnostics["status"] == "ok"


def test_discover_azd_environment_uses_default_environment(tmp_path: Path) -> None:
    env_dir = tmp_path / ".azure" / "qa"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text("AZURE_RESOURCE_GROUP=rg-qa", encoding="utf-8")
    (tmp_path / ".azure" / "config.json").write_text(
        '{"defaultEnvironment": "qa"}',
        encoding="utf-8",
    )

    env_name, values, diagnostics = _discover_azd_environment(tmp_path)

    assert env_name == "qa"
    assert values["AZURE_RESOURCE_GROUP"] == "rg-qa"
    assert diagnostics["status"] == "ok"


def test_discover_azd_environment_reports_ambiguous_envs(tmp_path: Path) -> None:
    for name in ("dev", "prod"):
        env_dir = tmp_path / ".azure" / name
        env_dir.mkdir(parents=True)
        (env_dir / ".env").write_text("AZURE_RESOURCE_GROUP=rg", encoding="utf-8")

    env_name, values, diagnostics = _discover_azd_environment(tmp_path)

    assert env_name is None
    assert values == {}
    assert diagnostics["status"] == "ambiguous"
    assert set(diagnostics["candidates"]) == {"dev", "prod"}


def test_select_account_matches_exact_endpoint_host() -> None:
    expected = _account("ai-prod", endpoint="https://ai-prod.openai.azure.com/")
    other = _account("ai-other", endpoint="https://ai-other.openai.azure.com/")

    selected, diagnostics = _select_account(
        [other, expected],
        endpoint_host="ai-prod.openai.azure.com",
    )

    assert selected is expected
    assert diagnostics["status"] == "matched"
    assert diagnostics["endpoint_match"] == "exact"


def test_select_account_matches_unique_subdomain() -> None:
    expected = _account("ai-prod", custom_subdomain_name="project-prod")
    other = _account("ai-other", custom_subdomain_name="project-other")

    selected, diagnostics = _select_account(
        [other, expected],
        endpoint_host="project-prod.services.ai.azure.com",
    )

    assert selected is expected
    assert diagnostics["status"] == "matched"
    assert diagnostics["endpoint_match"] == "subdomain"


def test_select_account_reports_ambiguous_without_endpoint_match() -> None:
    selected, diagnostics = _select_account(
        [_account("ai-1"), _account("ai-2")],
        endpoint_host=None,
    )

    assert selected is None
    assert diagnostics["status"] == "ambiguous"


def test_sources_are_enabled_by_default() -> None:
    assert AzureResourcesSourceConfig().enabled is True
    assert PostureCheckConfig().enabled is True


def test_collect_azure_resources_skips_actionably_without_subscription(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)

    payload = collect_azure_resources(
        AzureResourcesSourceConfig(subscription_id_env="AZURE_SUBSCRIPTION_ID"),
        workspace=tmp_path,
    )

    assert payload.account is None
    assert payload.diagnostics["status"] == "skipped"
    assert "subscription_id" in payload.diagnostics["reason"]
    assert ".azure/<env>/.env" in payload.diagnostics["reason"]


def test_collect_azure_resources_uses_azd_resource_group_and_account(
    tmp_path: Path, monkeypatch
) -> None:
    env_dir = tmp_path / ".azure" / "dev"
    env_dir.mkdir(parents=True)
    (env_dir / ".env").write_text(
        "\n".join(
            [
                "AZURE_SUBSCRIPTION_ID=sub-1",
                "AZURE_RESOURCE_GROUP=rg-ai",
                "AZURE_OPENAI_RESOURCE_NAME=ai-prod",
            ]
        ),
        encoding="utf-8",
    )

    account = _account("ai-prod", resource_group="rg-ai")
    cs_client = SimpleNamespace(
        accounts=SimpleNamespace(
            get=lambda resource_group_name, account_name: account,
            list_by_resource_group=lambda resource_group_name: [account],
            list=lambda: [account],
        ),
        deployments=SimpleNamespace(list=lambda resource_group_name, account_name: []),
    )
    monitor_client = SimpleNamespace(
        diagnostic_settings=SimpleNamespace(list=lambda resource_uri: [])
    )
    _install_fake_azure_identity(monkeypatch)
    monkeypatch.setattr(
        azure_resources,
        "_build_clients",
        lambda credential, subscription_id: (cs_client, monitor_client),
    )

    payload = collect_azure_resources(AzureResourcesSourceConfig(), workspace=tmp_path)

    assert payload.account is not None
    assert payload.account.name == "ai-prod"
    assert payload.diagnostics["status"] == "ok"
    assert payload.diagnostics["resource_group"] == "rg-ai"
    assert payload.diagnostics["discovery"]["subscription_id"] == "azd"
    assert payload.diagnostics["discovery"]["account"] == "azd"


def test_collect_azure_resources_can_match_account_from_foundry_endpoint(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    account = _account(
        "ai-prod",
        endpoint="https://project-prod.services.ai.azure.com/",
        resource_group="rg-foundry",
    )
    cs_client = SimpleNamespace(
        accounts=SimpleNamespace(
            list=lambda: [account],
            list_by_resource_group=lambda resource_group_name: [account],
        ),
        deployments=SimpleNamespace(list=lambda resource_group_name, account_name: []),
    )
    monitor_client = SimpleNamespace(
        diagnostic_settings=SimpleNamespace(list=lambda resource_uri: [])
    )
    _install_fake_azure_identity(monkeypatch)
    monkeypatch.setattr(
        azure_resources,
        "_build_clients",
        lambda credential, subscription_id: (cs_client, monitor_client),
    )

    payload = collect_azure_resources(
        AzureResourcesSourceConfig(),
        workspace=tmp_path,
        project_endpoint="https://project-prod.services.ai.azure.com/projects/demo",
    )

    assert payload.account is not None
    assert payload.account.name == "ai-prod"
    assert payload.diagnostics["status"] == "ok"
    assert payload.diagnostics["resource_group"] == "rg-foundry"
    assert payload.diagnostics["discovery"]["account"] == "subscription_scan"


def _install_fake_azure_identity(monkeypatch) -> None:
    azure_module = sys.modules.get("azure") or types.ModuleType("azure")
    identity_module = types.ModuleType("azure.identity")

    class FakeCredential:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    identity_module.DefaultAzureCredential = FakeCredential
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)

