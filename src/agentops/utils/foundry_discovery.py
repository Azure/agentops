"""Discover Foundry-attached resources from a project endpoint.

Currently exposes one helper: :func:`resolve_appinsights_connection`,
which asks a Foundry project for the connection string of the
Application Insights resource attached to it. Used by
:func:`agentops.utils.telemetry.init_tracing` as a fallback when the
user has configured ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` but not the
explicit ``APPLICATIONINSIGHTS_CONNECTION_STRING`` env var.

All Azure SDK imports are lazy; the discovery is best-effort and never
raises into callers - a missing SDK, a 404, or any unexpected response
shape returns ``None`` and the caller falls back to its no-op path.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def resolve_appinsights_connection(project_endpoint: str) -> Optional[str]:
    """Return the App Insights connection string for *project_endpoint*.

    Returns ``None`` when:

    * the ``azure-ai-projects`` / ``azure-identity`` SDKs are missing;
    * the project has no Application Insights resource connected;
    * the SDK is too old to expose the telemetry helper;
    * any call fails (auth, network, 4xx/5xx).

    The function never raises into callers: tracing is observability, not
    a critical path.
    """
    if not project_endpoint:
        return None

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        log.debug(
            "azure-ai-projects / azure-identity not installed; "
            "skipping App Insights discovery"
        )
        return None

    try:
        credential = DefaultAzureCredential(
            exclude_developer_cli_credential=True,
        )
        client = AIProjectClient(
            endpoint=project_endpoint,
            credential=credential,
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("could not build AIProjectClient for discovery: %s", exc)
        return None

    telemetry_attr = getattr(client, "telemetry", None)
    if telemetry_attr is None:
        log.debug(
            "AIProjectClient has no .telemetry helper "
            "(azure-ai-projects too old); set "
            "APPLICATIONINSIGHTS_CONNECTION_STRING manually."
        )
        return None

    # The exact method name has shifted slightly across SDK versions; try
    # the documented one first, then a couple of known aliases.
    candidate_methods = (
        "get_connection_string",
        "get_application_insights_connection_string",
        "connection_string",
    )
    for name in candidate_methods:
        fn = getattr(telemetry_attr, name, None)
        if fn is None:
            continue
        try:
            value = fn() if callable(fn) else fn
        except Exception as exc:  # noqa: BLE001
            log.debug(
                "AIProjectClient.telemetry.%s raised %s; "
                "falling through to manual env var.",
                name,
                exc,
            )
            continue
        if isinstance(value, str) and value:
            return value

    log.debug(
        "AIProjectClient.telemetry did not yield a connection string; "
        "either no App Insights is attached to the project or the SDK "
        "shape is unrecognized."
    )
    return None


def resolve_appinsights_connection_from_env() -> Optional[str]:
    """Resolve using ``AZURE_AI_FOUNDRY_PROJECT_ENDPOINT`` if set."""
    endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        return None
    return resolve_appinsights_connection(endpoint)
