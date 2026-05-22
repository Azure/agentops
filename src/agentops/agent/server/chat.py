"""Chat orchestration: turns a Copilot user message into an SSE reply."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from agentops.agent.analyzer import analyze
from agentops.agent.config import AgentConfig
from agentops.agent.report import render_report, short_chat_summary
from agentops.agent.server.protocol import CopilotRequest, stream_markdown


def _intro_for(message: str) -> str:
    msg = (message or "").lower()
    if any(word in msg for word in ("regress", "drop", "score")):
        focus = "regressions"
    elif any(word in msg for word in ("latency", "slow", "p95")):
        focus = "latency"
    elif any(word in msg for word in ("error", "fail", "exception")):
        focus = "production errors"
    elif any(word in msg for word in ("safety", "harm", "violen")):
        focus = "content safety"
    else:
        focus = "agent health"
    return (
        f"I scanned your AgentOps eval history, Azure Monitor, and Foundry "
        f"control plane focused on **{focus}**.\n\n"
    )


def build_reply(workspace: Path, config: AgentConfig, request: CopilotRequest) -> str:
    user_message = request.last_user_message or ""
    result = analyze(workspace, config)
    intro = _intro_for(user_message)
    summary = short_chat_summary(result)
    report = render_report(result)
    return f"{intro}{summary}\n\n---\n\n{report}"


def stream_reply(
    workspace: Path, config: AgentConfig, request: CopilotRequest
) -> Iterable[bytes]:
    return stream_markdown(build_reply(workspace, config, request))
