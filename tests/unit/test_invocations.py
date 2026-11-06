"""Unit tests for target invocation helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request

import pytest

from agentops.core.agentops_config import AgentOpsConfig
from agentops.pipeline import invocations


def _config(**overrides: object) -> AgentOpsConfig:
    data = {
        "version": 1,
        "agent": "my-agent:1",
        "dataset": Path("data.jsonl"),
        **overrides,
    }
    return AgentOpsConfig(**data)


def test_project_endpoint_prefers_config_over_environment(monkeypatch):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-env.services.ai.azure.com/api/projects/p",
    )
    cfg = _config(
        project_endpoint="https://from-config.services.ai.azure.com/api/projects/p"
    )

    assert (
        invocations._project_endpoint(cfg)
        == "https://from-config.services.ai.azure.com/api/projects/p"
    )


def test_project_endpoint_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
        "https://from-env.services.ai.azure.com/api/projects/p",
    )

    assert (
        invocations._project_endpoint(_config())
        == "https://from-env.services.ai.azure.com/api/projects/p"
    )


def test_project_endpoint_requires_config_or_environment(monkeypatch):
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    with pytest.raises(RuntimeError, match="project_endpoint"):
        invocations._project_endpoint(_config())


_AGENT_BASE = "https://acct.services.ai.azure.com/api/projects/proj/agents/helpdeskbot"
_PROTOCOL_ROUTE = f"{_AGENT_BASE}/endpoint/protocols/openai/responses"


def test_hosted_responses_target_rewrites_version_pinned_identity_url():
    url, agent_reference = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/versions/11"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"
    assert agent_reference == {
        "type": "agent_reference",
        "name": "helpdeskbot",
        "version": "11",
    }


def test_hosted_responses_target_rewrites_unversioned_identity_url():
    url, agent_reference = invocations._foundry_hosted_responses_target(_AGENT_BASE)

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"
    assert agent_reference is None


def test_hosted_responses_target_preserves_existing_query_parameters():
    url, _ = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/endpoint/protocols/openai?api-version=2025-05-01&trace=on"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=2025-05-01&trace=on"


def test_hosted_responses_target_is_idempotent_on_full_route():
    configured = f"{_PROTOCOL_ROUTE}?api-version=v1"

    url, agent_reference = invocations._foundry_hosted_responses_target(configured)

    assert url == configured
    assert agent_reference is None


def test_hosted_responses_target_appends_responses_to_protocol_route():
    url, _ = invocations._foundry_hosted_responses_target(
        f"{_AGENT_BASE}/endpoint/protocols/openai"
    )

    assert url == f"{_PROTOCOL_ROUTE}?api-version=v1"


def test_hosted_responses_target_leaves_custom_endpoints_without_api_version():
    url, agent_reference = invocations._foundry_hosted_responses_target(
        "https://my-agent.azurewebsites.net/chat"
    )

    assert url == "https://my-agent.azurewebsites.net/chat/responses"
    assert agent_reference is None


@pytest.mark.parametrize("header", ["Authorization", "X-API-KEY"])
@pytest.mark.parametrize("destination", [
    "https://receiver.invalid/next",
    "http://trusted.example/next",
    "https://trusted.example:444/next",
    "https://trusted.example:0/next",
])
def test_authenticated_redirects_cannot_change_origin(header, destination):
    request = Request("https://trusted.example/start", headers={header: "dummy"})
    with pytest.raises(RuntimeError, match="another origin"):
        invocations._SameOriginRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, destination,
        )


def test_authenticated_same_origin_redirect_is_preserved():
    request = Request("https://trusted.example/start", headers={"Authorization": "Bearer dummy"})
    redirected = invocations._SameOriginRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://trusted.example:443/next",
    )
    assert redirected.get_header("Authorization") == "Bearer dummy"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("header", ["X-API-KEY", "Accept", "Content-Type", "User-Agent"])
def test_json_and_streaming_use_credential_safe_opener(monkeypatch, streaming, header):
    response = Mock()
    response.read.return_value = b'{"text":"ok"}'
    response.__iter__ = Mock(return_value=iter([b"ok"]))
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    build = Mock(return_value=opener)
    monkeypatch.setattr(invocations.urllib.request, "build_opener", build)
    monkeypatch.setattr(invocations.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unsafe opener"))
    invoke = invocations._http_request_stream if streaming else invocations._http_request_json
    invoke(method="GET", url="https://trusted.example", headers={header: "dummy"}, timeout=1)
    assert isinstance(build.call_args.args[0], invocations._SameOriginRedirectHandler)
    opener.open.assert_called_once()
