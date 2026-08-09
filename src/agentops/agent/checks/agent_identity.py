"""Agent 365 registration posture check.

``agentops doctor`` scores a workspace against Well-Architected rules, but it
had no visibility into whether the agent exists as a first-class identity in
Microsoft Entra. Without an agent identity blueprint the agent cannot be
governed by Microsoft Agent 365: it does not appear in the agent inventory,
Conditional Access cannot target it, and its traces cannot be correlated back
to an accountable owner.

The check is deliberately read-only and cheap:

* it first resolves the identity from the workspace record or the
  ``AGENTOPS_ENTRA_AGENT_ID`` environment variable, which costs nothing,
* it only calls Microsoft Graph when the workspace opts in via
  ``identity.verify: true``, because the lookup needs tenant admin consent
  that most workspaces will not have on day one,
* every Graph failure becomes a warning with a readable sentence, never a
  stack trace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from agentops.agent.findings import Category, Finding, Severity
from agentops.services.agent_identity import (
    REGISTRATION_DOCS_URL,
    AgentIdentityError,
    load_identity_config,
    lookup_blueprint,
    resolve_agent_id,
    resolve_display_name,
)

SOURCE_NAME = "agent_identity"


def run_agent_identity_check(workspace: Path) -> List[Finding]:
    """Report whether the agent is registered in Microsoft Agent 365."""

    workspace = Path(workspace)
    identity = load_identity_config(workspace)
    display_name = resolve_display_name(workspace) or workspace.resolve().name

    if resolve_agent_id(workspace):
        return []

    if _verify_enabled(identity):
        try:
            blueprint = lookup_blueprint(display_name)
        except AgentIdentityError as exc:
            return [_lookup_failed(display_name, str(exc))]
        if blueprint is not None:
            return [_registered_but_unrecorded(blueprint.app_id, display_name)]

    return [_not_registered(display_name)]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _not_registered(display_name: str) -> Finding:
    return Finding(
        id="agent_identity.not_registered",
        severity=Severity.WARNING,
        category=Category.SECURITY,
        title="Agent is not registered in Microsoft Agent 365",
        summary=(
            "No Entra Agent ID is recorded for this workspace, so the agent has "
            "no first-class identity in Microsoft Agent 365. Without it the agent "
            "is absent from the tenant agent inventory, Conditional Access cannot "
            "target it, and its traces cannot be attributed to an accountable owner."
        ),
        recommendation=(
            "Register the agent identity blueprint with "
            "'agentops agent register --sponsor <upn-or-object-id>'. Set "
            "'identity.sponsor' in agentops.yaml first so the registration is "
            f"reproducible in CI. Background: {REGISTRATION_DOCS_URL}"
        ),
        source=SOURCE_NAME,
        evidence={"display_name": display_name, "registered": False},
    )


def _registered_but_unrecorded(app_id: str, display_name: str) -> Finding:
    return Finding(
        id="agent_identity.not_recorded",
        severity=Severity.INFO,
        category=Category.SECURITY,
        title="Agent identity exists in Entra but is not recorded locally",
        summary=(
            f"Microsoft Entra has an agent identity blueprint named "
            f"'{display_name}', but this workspace has no local record of it. "
            "Traces and the release evidence bundle therefore cannot quote the "
            "Entra Agent ID."
        ),
        recommendation=(
            "Run 'agentops agent register' to adopt the existing blueprint into "
            "this workspace. The command is idempotent and will reuse the "
            "blueprint instead of creating a duplicate."
        ),
        source=SOURCE_NAME,
        evidence={"display_name": display_name, "app_id": app_id, "registered": True},
    )


def _lookup_failed(display_name: str, reason: str) -> Finding:
    return Finding(
        id="agent_identity.lookup_failed",
        severity=Severity.WARNING,
        category=Category.SECURITY,
        title="Agent 365 registration could not be verified",
        summary=(
            "AgentOps could not confirm whether this agent has an Entra Agent ID. "
            f"{reason}"
        ),
        recommendation=(
            "Grant the AgentIdentityBlueprint.Read.All application permission and "
            "admin consent, or set 'identity.verify: false' in agentops.yaml to "
            "rely on the locally recorded identity instead."
        ),
        source=SOURCE_NAME,
        evidence={"display_name": display_name, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _verify_enabled(identity: dict[str, Any]) -> bool:
    value = identity.get("verify")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


__all__ = ["SOURCE_NAME", "run_agent_identity_check"]
