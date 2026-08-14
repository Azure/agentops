"""Microsoft Entra Agent ID support for the Agent 365 control plane.

An agent produced by the accelerator is invisible to the Agent 365 control
plane until an *agent identity blueprint* exists for it in Microsoft Entra.
This module owns the three things AgentOps needs around that identity:

* a very small app-only Microsoft Graph client (:class:`GraphClient`),
* create/lookup of the blueprint (:func:`lookup_blueprint`,
  :func:`register_blueprint`),
* on-disk persistence of the resulting ``appId`` so the doctor check, the
  OTel resource attributes, and the release evidence bundle can all quote the
  same value without re-calling Graph.

Design notes
------------

**Read-only by default.** Nothing here runs implicitly. The doctor check does
a read-only lookup, and registration only happens when the operator asks for
it explicitly.

**No new dependency.** Graph is a plain REST API, so we use
:mod:`urllib.request` from the standard library rather than pulling in an SDK.
The credential comes from the same shared factory the doctor sources use.

**Errors are messages, not stack traces.** Every failure mode a user can
realistically hit (missing consent, missing role, no credential, package not
installed) is converted into an :class:`AgentIdentityError` carrying a
sentence that says what to do next.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

#: Graph endpoint AgentOps talks to. v1.0 only - the ``AgentIdentity*``
#: scopes this module needs are published there.
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

#: App-only token scope for Microsoft Graph.
GRAPH_SCOPE = "https://graph.microsoft.com/.default"

#: OData type discriminator for an agent identity blueprint.
BLUEPRINT_ODATA_TYPE = "Microsoft.Graph.AgentIdentityBlueprint"

#: OTel resource attribute carrying the Entra Agent ID.
AGENT_ID_ATTRIBUTE = "gen_ai.agent.id"

#: Environment override for the Entra Agent ID. CI exports this when the
#: identity is provisioned outside the workspace (for example by a platform
#: team) so traces still carry the right value without a local record.
AGENT_ID_ENV = "AGENTOPS_ENTRA_AGENT_ID"

#: Where the resolved identity is persisted inside a workspace.
IDENTITY_RECORD_RELPATH = Path(".agentops") / "identity" / "agent-identity.json"

#: Documentation pointer used in remediation text.
REGISTRATION_DOCS_URL = (
    "https://learn.microsoft.com/entra/identity/agent-id/agent-id-overview"
)

#: Deep link template for an application object in the Entra admin center.
ENTRA_APP_DEEPLINK = (
    "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps"
    "/ApplicationMenuBlade/~/Overview/appId/{app_id}"
)


class AgentIdentityError(RuntimeError):
    """A user-actionable failure while talking to Microsoft Graph.

    The message is expected to be shown verbatim to the operator, so it
    always explains the remediation rather than quoting an HTTP status.
    """


@dataclass(frozen=True)
class AgentIdentityBlueprint:
    """An agent identity blueprint as Agent 365 sees it."""

    app_id: str
    object_id: Optional[str] = None
    display_name: Optional[str] = None

    @property
    def portal_url(self) -> str:
        return ENTRA_APP_DEEPLINK.format(app_id=self.app_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "object_id": self.object_id,
            "display_name": self.display_name,
            "portal_url": self.portal_url,
        }


# ---------------------------------------------------------------------------
# Graph client
# ---------------------------------------------------------------------------


class GraphClient:
    """Minimal app-only Microsoft Graph client.

    Only the two verbs this feature needs are implemented. Tests inject a
    stand-in with the same ``get``/``post`` shape rather than patching
    :mod:`urllib`.
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        base_url: str = GRAPH_BASE_URL,
        timeout: int = 30,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    # -- public API ------------------------------------------------------

    def get(self, path: str, *, params: Optional[Mapping[str, str]] = None) -> Any:
        url = self._url(path)
        if params:
            url = f"{url}?{urllib.parse.urlencode(dict(params))}"
        return self._request("GET", url, body=None)

    def post(self, path: str, body: Mapping[str, Any]) -> Any:
        return self._request("POST", self._url(path), body=dict(body))

    # -- internals -------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolve_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Agent identity blueprints are an OData-typed resource; Graph
            # rejects the payload without the version header.
            "OData-Version": "4.0",
        }

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        self._token = acquire_graph_token()
        return self._token

    def _request(self, method: str, url: str, *, body: Optional[dict[str, Any]]) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url, data=data, method=method, headers=self._headers()
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _http_error(exc) from None
        except urllib.error.URLError as exc:
            raise AgentIdentityError(
                "Could not reach Microsoft Graph "
                f"({exc.reason}). Check network connectivity or proxy settings."
            ) from None
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise AgentIdentityError(
                "Microsoft Graph returned a response that is not valid JSON."
            ) from None


def _http_error(exc: urllib.error.HTTPError) -> AgentIdentityError:
    """Translate an HTTP failure into an actionable message."""

    detail = _graph_error_message(exc)
    status = exc.code
    if status in (401,):
        return AgentIdentityError(
            "Microsoft Graph rejected the credential (401). Sign in with an "
            "identity that has app-only access to Graph, or set the "
            "AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET "
            f"environment variables in CI. {detail}".strip()
        )
    if status in (403,):
        return AgentIdentityError(
            "Microsoft Graph denied the request (403). The app registration is "
            "missing admin consent for AgentIdentityBlueprint.Read.All (lookup) "
            "or AgentIdentityBlueprint.Create (registration). Ask a tenant "
            f"admin to grant consent, then retry. {detail}".strip()
        )
    if status == 404:
        return AgentIdentityError(
            "Microsoft Graph returned 404 for the agent identity endpoint. The "
            "tenant may not be enrolled in Microsoft Agent 365 yet. "
            f"See {REGISTRATION_DOCS_URL}. {detail}".strip()
        )
    if status == 429:
        return AgentIdentityError(
            "Microsoft Graph throttled the request (429). Retry in a few "
            f"seconds. {detail}".strip()
        )
    return AgentIdentityError(
        f"Microsoft Graph request failed with HTTP {status}. {detail}".strip()
    )


def _graph_error_message(exc: urllib.error.HTTPError) -> str:
    """Best-effort extraction of the Graph ``error.message`` field."""

    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - error bodies are unreliable
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return f"Graph said: {message.strip()}"
    return ""


def acquire_graph_token() -> str:
    """Return an app-only Microsoft Graph access token.

    Reuses the shared doctor credential so a process that already
    authenticated for Azure Monitor does not walk the credential chain again.
    """

    try:
        from agentops.agent.sources._credentials import (
            format_source_error,
            get_shared_credential,
        )
    except ImportError:  # pragma: no cover - package layout guarantees this
        raise AgentIdentityError(
            "AgentOps could not load its credential helper."
        ) from None

    try:
        credential = get_shared_credential()
    except ImportError:
        raise AgentIdentityError(
            "The 'azure-identity' package is required to talk to Microsoft "
            "Graph. Install it with: pip install azure-identity"
        ) from None

    try:
        token = credential.get_token(GRAPH_SCOPE)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean message
        raise AgentIdentityError(
            "Could not acquire a Microsoft Graph token: "
            f"{format_source_error(exc)}"
        ) from None

    value = getattr(token, "token", None)
    if not value:
        raise AgentIdentityError(
            "The credential returned an empty Microsoft Graph token."
        )
    return str(value)


# ---------------------------------------------------------------------------
# Blueprint lookup / registration
# ---------------------------------------------------------------------------


def _escape_odata_literal(value: str) -> str:
    """Escape a string for use inside an OData ``$filter`` literal."""

    return value.replace("'", "''")


def _blueprint_from_payload(payload: Mapping[str, Any]) -> Optional[AgentIdentityBlueprint]:
    app_id = payload.get("appId")
    if not isinstance(app_id, str) or not app_id.strip():
        return None
    object_id = payload.get("id")
    display_name = payload.get("displayName")
    return AgentIdentityBlueprint(
        app_id=app_id.strip(),
        object_id=object_id if isinstance(object_id, str) else None,
        display_name=display_name if isinstance(display_name, str) else None,
    )


def lookup_blueprint(
    display_name: str,
    *,
    client: Optional[GraphClient] = None,
) -> Optional[AgentIdentityBlueprint]:
    """Return the agent identity blueprint named ``display_name``, if any.

    Read-only. Returns ``None`` when the tenant has no blueprint with that
    display name; raises :class:`AgentIdentityError` when Graph could not be
    consulted at all (so callers can tell "not registered" apart from
    "could not check").
    """

    name = (display_name or "").strip()
    if not name:
        raise AgentIdentityError(
            "An agent display name is required to look up its identity."
        )

    graph = client or GraphClient()
    payload = graph.get(
        "/applications",
        params={
            "$filter": f"displayName eq '{_escape_odata_literal(name)}'",
            "$select": "id,appId,displayName",
            "$top": "1",
        },
    )
    values = payload.get("value") if isinstance(payload, Mapping) else None
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, Mapping):
        return None
    return _blueprint_from_payload(first)


def register_blueprint(
    display_name: str,
    *,
    sponsor: str,
    client: Optional[GraphClient] = None,
) -> tuple[AgentIdentityBlueprint, bool]:
    """Create the agent identity blueprint, or return the existing one.

    Returns ``(blueprint, created)`` where ``created`` is ``False`` when a
    blueprint with the same display name already existed. Re-running is
    therefore safe: no duplicate is ever created.
    """

    name = (display_name or "").strip()
    if not name:
        raise AgentIdentityError(
            "An agent display name is required to register an agent identity."
        )
    if not (sponsor or "").strip():
        raise AgentIdentityError(
            "A sponsor is required to register an agent identity. Set "
            "'identity.sponsor' in agentops.yaml to the object id or UPN of "
            "the human accountable for this agent."
        )

    graph = client or GraphClient()

    existing = lookup_blueprint(name, client=graph)
    if existing is not None:
        return existing, False

    payload = graph.post(
        "/applications",
        {
            "@odata.type": BLUEPRINT_ODATA_TYPE,
            "displayName": name,
            "sponsors": [sponsor.strip()],
        },
    )
    if not isinstance(payload, Mapping):
        raise AgentIdentityError(
            "Microsoft Graph accepted the registration but returned no "
            "application object."
        )
    blueprint = _blueprint_from_payload(payload)
    if blueprint is None:
        raise AgentIdentityError(
            "Microsoft Graph accepted the registration but returned no appId."
        )
    return blueprint, True


# ---------------------------------------------------------------------------
# Workspace persistence
# ---------------------------------------------------------------------------


def identity_record_path(workspace: Path) -> Path:
    return Path(workspace) / IDENTITY_RECORD_RELPATH


def write_identity_record(
    workspace: Path,
    blueprint: AgentIdentityBlueprint,
    *,
    created: bool = False,
) -> Path:
    """Persist ``blueprint`` under ``.agentops/identity/`` and return the path."""

    path = identity_record_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "created": bool(created),
        **blueprint.to_dict(),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def read_identity_record(workspace: Path) -> Optional[dict[str, Any]]:
    """Return the persisted identity record, or ``None`` when absent/invalid."""

    path = identity_record_path(workspace)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def resolve_agent_id(workspace: Optional[Path] = None) -> Optional[str]:
    """Return the Entra Agent ID for this workspace.

    Resolution order: the ``AGENTOPS_ENTRA_AGENT_ID`` environment variable
    (so CI can inject an identity provisioned elsewhere), then the persisted
    record. Returns ``None`` when the agent has no known identity, which is
    the normal state before registration.
    """

    override = os.environ.get(AGENT_ID_ENV, "").strip()
    if override and "$(" not in override and "${{" not in override:
        return override
    if workspace is None:
        return None
    record = read_identity_record(workspace)
    if not record:
        return None
    app_id = record.get("app_id")
    return app_id.strip() if isinstance(app_id, str) and app_id.strip() else None


# ---------------------------------------------------------------------------
# Workspace configuration
# ---------------------------------------------------------------------------


def load_identity_config(workspace: Path) -> dict[str, Any]:
    """Return the ``identity`` block from ``agentops.yaml``, or ``{}``."""

    path = Path(workspace) / "agentops.yaml"
    if not path.exists():
        return {}
    try:
        from agentops.utils.yaml import load_yaml

        data = load_yaml(path)
    except Exception:  # noqa: BLE001 - config problems must not crash callers
        return {}
    if not isinstance(data, dict):
        return {}
    identity = data.get("identity")
    return identity if isinstance(identity, dict) else {}


def resolve_display_name(
    workspace: Path, *, override: Optional[str] = None
) -> Optional[str]:
    """Resolve the blueprint display name for ``workspace``.

    Precedence: explicit override, then ``identity.display_name``, then a
    previously recorded name, then the agent target name from
    ``agentops.yaml``. Returns ``None`` when nothing usable is configured.
    """

    if isinstance(override, str) and override.strip():
        return override.strip()

    identity = load_identity_config(workspace)
    configured = identity.get("display_name")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()

    record = read_identity_record(workspace) or {}
    recorded = record.get("display_name")
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip()

    return _name_from_target(workspace)


def _name_from_target(workspace: Path) -> Optional[str]:
    path = Path(workspace) / "agentops.yaml"
    if not path.exists():
        return None
    try:
        from agentops.utils.yaml import load_yaml

        data = load_yaml(path)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    agent = data.get("agent")
    if not isinstance(agent, str):
        return None
    raw = agent.strip()
    if not raw:
        return None
    try:
        from agentops.core.agentops_config import classify_agent

        target = classify_agent(raw)
    except ValueError:
        return None
    return target.name


def resolve_registration_inputs(
    workspace: Path,
    *,
    display_name: Optional[str] = None,
    sponsor: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve ``(display_name, sponsor)`` for a registration call.

    Raises :class:`AgentIdentityError` with an actionable sentence when
    either value is missing, because Agent 365 rejects a blueprint that has
    no name and AgentOps refuses to create one that has no accountable owner.
    """

    identity = load_identity_config(workspace)

    resolved_name = resolve_display_name(workspace, override=display_name)
    if not resolved_name:
        raise AgentIdentityError(
            "No display name for the agent identity. Pass --display-name, or "
            "set 'identity.display_name' in agentops.yaml."
        )

    resolved_sponsor = sponsor if isinstance(sponsor, str) else None
    if not (resolved_sponsor and resolved_sponsor.strip()):
        configured = identity.get("sponsor")
        resolved_sponsor = configured if isinstance(configured, str) else None
    if not (resolved_sponsor and resolved_sponsor.strip()):
        raise AgentIdentityError(
            "No sponsor for the agent identity. Microsoft Agent 365 requires an "
            "accountable owner. Pass --sponsor <upn-or-object-id>, or set "
            "'identity.sponsor' in agentops.yaml."
        )

    return resolved_name, resolved_sponsor.strip()


__all__ = [
    "AGENT_ID_ATTRIBUTE",
    "AGENT_ID_ENV",
    "AgentIdentityBlueprint",
    "AgentIdentityError",
    "GraphClient",
    "REGISTRATION_DOCS_URL",
    "acquire_graph_token",
    "identity_record_path",
    "load_identity_config",
    "lookup_blueprint",
    "read_identity_record",
    "register_blueprint",
    "resolve_agent_id",
    "resolve_display_name",
    "resolve_registration_inputs",
    "write_identity_record",
]
