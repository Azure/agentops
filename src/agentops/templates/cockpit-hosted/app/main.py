"""Hosted Cockpit entrypoint for Azure App Service (azd host: appservice).

This module is the `app.main:app` target invoked by gunicorn/uvicorn on the
App Service Linux worker (see `infra/main.bicep`'s `appCommandLine`). It only
wires together the already-installed `agentops-accelerator[cockpit]` package;
it contains no application logic, no Azure SDK calls, and no additional HTTP
routes of its own — `/healthz` and every other hosted route are served by
`agentops.agent.cockpit.create_app`.

Per repository convention, Azure/network-touching imports stay lazy (deferred
until `create_hosted_app()` runs) so importing this module never has side
effects beyond setting a default environment variable.
"""

from __future__ import annotations

import inspect
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentops.cockpit.hosted")

# Hosted mode is the default for this entrypoint; an operator can still
# override it (for example during local smoke testing of this exact file)
# by exporting AGENTOPS_COCKPIT_MODE before the process starts.
os.environ.setdefault("AGENTOPS_COCKPIT_MODE", "hosted")


def _resolve_workspace() -> Path:
    """Resolve the AgentOps workspace directory for the hosted Cockpit.

    Defaults to the current working directory, matching how App Service
    starts the worker inside the deployed application root.
    """
    raw = os.environ.get("AGENTOPS_WORKSPACE", "").strip()
    return Path(raw) if raw else Path.cwd()


def create_hosted_app() -> Any:
    """Build the FastAPI application from the installed agentops package.

    The import is deferred to keep module import side-effect free and to
    ensure we always bind against whatever version of
    `agentops-accelerator` was installed by `requirements.txt.tmpl` on the
    App Service worker, not a copy bundled into this template.
    """
    from agentops.agent.cockpit import create_app  # lazy: installed package

    workspace = _resolve_workspace()
    signature = inspect.signature(create_app)
    if "mode" in signature.parameters:
        return create_app(workspace, mode="hosted")
    return create_app(workspace)


app = create_hosted_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
