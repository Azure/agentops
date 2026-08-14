"""Tests for the Entra agent identity service.

Every Graph call is exercised through an injected double, so the suite never
needs a tenant, a token, or network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from agentops.services.agent_identity import (
    AGENT_ID_ATTRIBUTE,
    AGENT_ID_ENV,
    AgentIdentityBlueprint,
    AgentIdentityError,
    identity_record_path,
    load_identity_config,
    lookup_blueprint,
    read_identity_record,
    register_blueprint,
    resolve_agent_id,
    resolve_display_name,
    resolve_registration_inputs,
    write_identity_record,
)


class FakeGraphClient:
    """Records calls and replays canned responses."""

    def __init__(
        self,
        *,
        get_response: Mapping[str, Any] | None = None,
        post_response: Mapping[str, Any] | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self._get_response = get_response if get_response is not None else {"value": []}
        self._post_response = post_response or {}
        self._get_error = get_error
        self.get_calls: list[tuple[str, dict[str, str] | None]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, str] | None = None) -> Mapping[str, Any]:
        self.get_calls.append((path, params))
        if self._get_error is not None:
            raise self._get_error
        return self._get_response

    def post(self, path: str, body: dict[str, Any]) -> Mapping[str, Any]:
        self.post_calls.append((path, body))
        return self._post_response


BLUEPRINT_PAYLOAD = {
    "id": "object-1",
    "appId": "app-1",
    "displayName": "support-agent",
}


def test_agent_id_attribute_matches_semantic_convention() -> None:
    assert AGENT_ID_ATTRIBUTE == "gen_ai.agent.id"


# ---------------------------------------------------------------------------
# lookup_blueprint
# ---------------------------------------------------------------------------


def test_lookup_returns_none_when_tenant_has_no_blueprint() -> None:
    client = FakeGraphClient(get_response={"value": []})
    assert lookup_blueprint("support-agent", client=client) is None
    path, params = client.get_calls[0]
    assert path == "/applications"
    assert params is not None
    assert params["$filter"] == "displayName eq 'support-agent'"


def test_lookup_returns_blueprint_when_found() -> None:
    client = FakeGraphClient(get_response={"value": [BLUEPRINT_PAYLOAD]})
    blueprint = lookup_blueprint("support-agent", client=client)
    assert blueprint is not None
    assert blueprint.app_id == "app-1"
    assert blueprint.object_id == "object-1"
    assert blueprint.display_name == "support-agent"


def test_lookup_escapes_single_quotes_in_display_name() -> None:
    client = FakeGraphClient(get_response={"value": []})
    lookup_blueprint("o'brien agent", client=client)
    _, params = client.get_calls[0]
    assert params is not None
    assert params["$filter"] == "displayName eq 'o''brien agent'"


def test_lookup_rejects_blank_display_name() -> None:
    with pytest.raises(AgentIdentityError):
        lookup_blueprint("   ", client=FakeGraphClient())


def test_lookup_propagates_graph_errors() -> None:
    boom = AgentIdentityError("consent missing")
    client = FakeGraphClient(get_error=boom)
    with pytest.raises(AgentIdentityError, match="consent missing"):
        lookup_blueprint("support-agent", client=client)


# ---------------------------------------------------------------------------
# register_blueprint
# ---------------------------------------------------------------------------


def test_register_creates_blueprint_when_absent() -> None:
    client = FakeGraphClient(
        get_response={"value": []},
        post_response=BLUEPRINT_PAYLOAD,
    )
    blueprint, created = register_blueprint(
        "support-agent", sponsor="paulo@contoso.com", client=client
    )
    assert created is True
    assert blueprint.app_id == "app-1"
    path, body = client.post_calls[0]
    assert path == "/applications"
    assert body["displayName"] == "support-agent"
    assert body["sponsors"] == ["paulo@contoso.com"]


def test_register_is_idempotent_when_blueprint_exists() -> None:
    client = FakeGraphClient(get_response={"value": [BLUEPRINT_PAYLOAD]})
    blueprint, created = register_blueprint(
        "support-agent", sponsor="paulo@contoso.com", client=client
    )
    assert created is False
    assert blueprint.app_id == "app-1"
    assert client.post_calls == []


def test_register_requires_a_sponsor() -> None:
    with pytest.raises(AgentIdentityError, match="sponsor"):
        register_blueprint("support-agent", sponsor="  ", client=FakeGraphClient())


def test_register_requires_a_display_name() -> None:
    with pytest.raises(AgentIdentityError, match="display name"):
        register_blueprint("", sponsor="paulo@contoso.com", client=FakeGraphClient())


def test_register_fails_loudly_when_graph_returns_no_app_id() -> None:
    client = FakeGraphClient(get_response={"value": []}, post_response={"id": "x"})
    with pytest.raises(AgentIdentityError, match="appId"):
        register_blueprint("support-agent", sponsor="p@c.com", client=client)


# ---------------------------------------------------------------------------
# Workspace persistence
# ---------------------------------------------------------------------------


def test_write_and_read_identity_record(tmp_path: Path) -> None:
    blueprint = AgentIdentityBlueprint(
        app_id="app-1", object_id="object-1", display_name="support-agent"
    )
    path = write_identity_record(tmp_path, blueprint, created=True)
    assert path == identity_record_path(tmp_path)
    record = read_identity_record(tmp_path)
    assert record is not None
    assert record["app_id"] == "app-1"
    assert record["created"] is True
    assert record["version"] == 1


def test_read_identity_record_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_identity_record(tmp_path) is None


def test_read_identity_record_tolerates_corrupt_json(tmp_path: Path) -> None:
    path = identity_record_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_identity_record(tmp_path) is None


def test_resolve_agent_id_prefers_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_identity_record(tmp_path, AgentIdentityBlueprint(app_id="from-record"))
    monkeypatch.setenv(AGENT_ID_ENV, "from-env")
    assert resolve_agent_id(tmp_path) == "from-env"


def test_resolve_agent_id_ignores_unexpanded_ci_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_identity_record(tmp_path, AgentIdentityBlueprint(app_id="from-record"))
    monkeypatch.setenv(AGENT_ID_ENV, "${{ secrets.AGENT_ID }}")
    assert resolve_agent_id(tmp_path) == "from-record"


def test_resolve_agent_id_returns_none_before_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(AGENT_ID_ENV, raising=False)
    assert resolve_agent_id(tmp_path) is None


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def _write_config(workspace: Path, body: str) -> None:
    (workspace / "agentops.yaml").write_text(body, encoding="utf-8")


def test_load_identity_config_returns_empty_without_config(tmp_path: Path) -> None:
    assert load_identity_config(tmp_path) == {}


def test_load_identity_config_reads_identity_block(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity:\n  sponsor: paulo@contoso.com\n")
    assert load_identity_config(tmp_path)["sponsor"] == "paulo@contoso.com"


def test_load_identity_config_tolerates_malformed_yaml(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity: [unclosed\n")
    assert load_identity_config(tmp_path) == {}


def test_resolve_display_name_prefers_explicit_override(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity:\n  display_name: from-config\n")
    assert resolve_display_name(tmp_path, override="from-flag") == "from-flag"


def test_resolve_display_name_falls_back_to_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity:\n  display_name: from-config\n")
    assert resolve_display_name(tmp_path) == "from-config"


def test_resolve_display_name_falls_back_to_existing_record(tmp_path: Path) -> None:
    write_identity_record(
        tmp_path, AgentIdentityBlueprint(app_id="app-1", display_name="from-record")
    )
    assert resolve_display_name(tmp_path) == "from-record"


def test_resolve_display_name_from_hosted_agent_url(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "agent: https://example.services.ai.azure.com/api/projects/demo/"
        "agents/helpdeskbot/versions/13\n",
    )

    assert resolve_display_name(tmp_path) == "helpdeskbot"


def test_resolve_registration_inputs_returns_name_and_sponsor(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "identity:\n  display_name: support-agent\n  sponsor: paulo@contoso.com\n",
    )
    name, sponsor = resolve_registration_inputs(tmp_path)
    assert name == "support-agent"
    assert sponsor == "paulo@contoso.com"


def test_resolve_registration_inputs_requires_a_display_name(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity:\n  sponsor: paulo@contoso.com\n")
    with pytest.raises(AgentIdentityError, match="display name"):
        resolve_registration_inputs(tmp_path)


def test_resolve_registration_inputs_requires_a_sponsor(tmp_path: Path) -> None:
    _write_config(tmp_path, "identity:\n  display_name: support-agent\n")
    with pytest.raises(AgentIdentityError, match="sponsor"):
        resolve_registration_inputs(tmp_path)


def test_resolve_registration_inputs_accepts_overrides(tmp_path: Path) -> None:
    name, sponsor = resolve_registration_inputs(
        tmp_path, display_name="flag-agent", sponsor="flag@contoso.com"
    )
    assert (name, sponsor) == ("flag-agent", "flag@contoso.com")


def test_identity_record_is_valid_json_on_disk(tmp_path: Path) -> None:
    write_identity_record(tmp_path, AgentIdentityBlueprint(app_id="app-1"))
    raw = identity_record_path(tmp_path).read_text(encoding="utf-8")
    assert json.loads(raw)["app_id"] == "app-1"
