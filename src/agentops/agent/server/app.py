"""FastAPI factory for the Copilot Extension server.

Exposes:
- ``POST /agents/messages`` — Copilot Extensions protocol (SSE response)
- ``GET /healthz``         — liveness check
- ``GET /``                — small HTML index
"""

import logging
from pathlib import Path

from agentops.agent.config import AgentConfig

log = logging.getLogger(__name__)


def create_app(
    workspace: Path,
    config: AgentConfig,
    verify_signature: bool = True,
):
    """Build a FastAPI app for the watchdog Copilot Extension server."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "FastAPI is not installed. Install agent extras with "
            "'pip install agentops-toolkit[agent]'."
        ) from exc

    from agentops.agent.server.chat import stream_reply
    from agentops.agent.server.protocol import parse_copilot_request

    app = FastAPI(title="AgentOps Watchdog", version="1.0")

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (
            "<!doctype html><html><body>"
            "<h1>AgentOps Watchdog</h1>"
            "<p>Copilot Extension endpoint: <code>POST /agents/messages</code></p>"
            "<p>Health: <a href='/healthz'>/healthz</a></p>"
            "</body></html>"
        )

    @app.post("/agents/messages")
    async def messages(request: Request):
        body_bytes = await request.body()

        if verify_signature:
            from agentops.agent.server.auth import verify_signature as _verify

            try:
                _verify(
                    body_bytes,
                    request.headers.get("x-github-public-key-identifier"),
                    request.headers.get("x-github-public-key-signature"),
                )
            except ValueError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"invalid JSON body: {exc}"
            ) from exc

        copilot_request = parse_copilot_request(
            payload if isinstance(payload, dict) else {}
        )

        def _generator():
            for chunk in stream_reply(workspace, config, copilot_request):
                yield chunk

        return StreamingResponse(_generator(), media_type="text/event-stream")

    return app
