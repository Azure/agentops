"""GitHub Copilot Extensions request/response protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import json


@dataclass
class CopilotMessage:
    role: str
    content: str


@dataclass
class CopilotRequest:
    messages: List[CopilotMessage]
    raw: Dict[str, Any]

    @property
    def last_user_message(self) -> Optional[str]:
        for message in reversed(self.messages):
            if message.role == "user" and message.content:
                return message.content
        return None


def parse_copilot_request(body: Dict[str, Any]) -> CopilotRequest:
    raw_messages = body.get("messages") or []
    messages: List[CopilotMessage] = []
    if isinstance(raw_messages, list):
        for entry in raw_messages:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role") or "user")
            content = entry.get("content") or ""
            if isinstance(content, list):
                # Multipart content -> concatenate text parts.
                parts: List[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("text", "")))
                content = "".join(parts)
            messages.append(CopilotMessage(role=role, content=str(content)))
    return CopilotRequest(messages=messages, raw=body)


def sse_text_chunk(text: str) -> bytes:
    payload = {
        "choices": [
            {
                "delta": {"role": "assistant", "content": text},
                "index": 0,
            }
        ]
    }
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def sse_done() -> bytes:
    return b"data: [DONE]\n\n"


def stream_markdown(markdown: str, chunk_size: int = 512) -> Iterable[bytes]:
    """Yield SSE chunks for a Markdown reply, then a [DONE] sentinel."""
    if not markdown:
        markdown = "_(empty reply)_"
    for start in range(0, len(markdown), chunk_size):
        yield sse_text_chunk(markdown[start : start + chunk_size])
    yield sse_done()
