"""Unit tests for streaming HTTP/JSON invocation support.

Covers the ``response_mode: sse|text`` aggregation paths, the configurable
auth header, and a guard that ``response_mode: json`` (the default) keeps the
existing behavior byte-for-byte. urllib is mocked; no network is used.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import pytest

from agentops.core.agentops_config import AgentOpsConfig, StreamConfig
from agentops.pipeline import invocations
from agentops.pipeline.invocations import InvocationResult


def _config(**overrides: Any) -> AgentOpsConfig:
    base: Dict[str, Any] = {
        "version": 1,
        "agent": "https://app.example.com/orchestrator",
        "dataset": "./qa.jsonl",
    }
    base.update(overrides)
    return AgentOpsConfig(**base)


class _FakeStreamResponse:
    """Context-manager response that yields decoded lines like HTTPResponse."""

    def __init__(self, lines: List[bytes]) -> None:
        self._lines = lines

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def __iter__(self):
        return iter(self._lines)


class _FakeJsonResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeJsonResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _patch_urlopen(monkeypatch, response, captured: Optional[Dict[str, Any]] = None):
    @contextmanager
    def _fake(request, timeout=None):  # noqa: ANN001
        if captured is not None:
            captured["request"] = request
            captured["timeout"] = timeout
            captured["headers"] = dict(request.headers)
            captured["body"] = (
                json.loads(request.data.decode("utf-8")) if request.data else None
            )
        yield response

    # urlopen is used as ``with urllib.request.urlopen(...) as r``; a context
    # manager factory mirrors the stdlib contract.
    monkeypatch.setattr(invocations.urllib.request, "urlopen", _fake)


def _invoke(config: AgentOpsConfig, row: Dict[str, Any]) -> InvocationResult:
    target = config.resolved_target()
    return invocations.invoke(target, config, row, timeout=5.0)


# ---------------------------------------------------------------------------
# (a) SSE with JSON data lines, text_field + done_marker
# ---------------------------------------------------------------------------


def test_sse_json_data_with_text_field_and_done_marker(monkeypatch) -> None:
    lines = [
        b'data: {"delta": {"content": "Hello"}}\n',
        b'data: {"delta": {"content": ", world"}}\n',
        b"data: [DONE]\n",
        b'data: {"delta": {"content": "IGNORED"}}\n',
    ]
    _patch_urlopen(monkeypatch, _FakeStreamResponse(lines))

    config = _config(
        request_field="ask",
        response_mode="sse",
        stream=StreamConfig(text_field="delta.content", done_marker="[DONE]"),
    )
    result = _invoke(config, {"input": "hi"})

    assert result.response == "Hello, world"
    assert result.tool_calls is None
    assert result.latency_seconds >= 0.0


def test_sse_error_event_raises(monkeypatch) -> None:
    lines = [
        b"event: error\n",
        b'data: backend exploded\n',
        b"\n",
    ]
    _patch_urlopen(monkeypatch, _FakeStreamResponse(lines))

    config = _config(response_mode="sse")
    with pytest.raises(RuntimeError, match="error event"):
        _invoke(config, {"input": "hi"})


# ---------------------------------------------------------------------------
# (b) Raw-text stream with a leading conversation_id token (orchestrator case)
# ---------------------------------------------------------------------------


def test_text_stream_strips_leading_conversation_id(monkeypatch) -> None:
    # The orchestrator emits "<conversation_id> " as the first chunk, then
    # raw token deltas. Line iteration reconstructs the full body.
    lines = [b"abc-123 The answer is 42."]
    _patch_urlopen(monkeypatch, _FakeStreamResponse(lines))

    config = _config(
        request_field="ask",
        response_mode="text",
        stream=StreamConfig(strip_leading_token=True),
    )
    result = _invoke(config, {"input": "what is it"})

    assert result.response == "The answer is 42."


def test_text_stream_without_strip_keeps_everything(monkeypatch) -> None:
    lines = [b"plain streamed answer"]
    _patch_urlopen(monkeypatch, _FakeStreamResponse(lines))

    config = _config(response_mode="text")
    result = _invoke(config, {"input": "x"})

    assert result.response == "plain streamed answer"


# ---------------------------------------------------------------------------
# (c) response_mode: json (default) is unchanged
# ---------------------------------------------------------------------------


def test_default_json_mode_unchanged(monkeypatch) -> None:
    payload = json.dumps({"text": "echo: hi"}).encode("utf-8")
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload))

    config = _config()  # response_mode defaults to "json"
    assert config.response_mode == "json"
    result = _invoke(config, {"input": "hi"})

    assert result.response == "echo: hi"


def test_json_parse_failure_mentions_response_mode(monkeypatch) -> None:
    # A streaming endpoint hit with the default json mode should fail with a
    # message that points the user at response_mode: sse|text.
    _patch_urlopen(monkeypatch, _FakeJsonResponse(b"data: not json\n\n"))

    config = _config()
    with pytest.raises(RuntimeError, match="response_mode: sse"):
        _invoke(config, {"input": "hi"})


# ---------------------------------------------------------------------------
# (d) Auth header: new X-API-KEY path + legacy Authorization: Bearer default
# ---------------------------------------------------------------------------


def test_custom_auth_header_emitted(monkeypatch) -> None:
    monkeypatch.setenv("ORCH_KEY", "s3cret")
    payload = json.dumps({"text": "ok"}).encode("utf-8")
    captured: Dict[str, Any] = {}
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload), captured)

    config = _config(
        request_field="ask",
        auth_header_env="ORCH_KEY",
        auth_header_name="X-API-KEY",
        auth_value_template="{token}",
    )
    _invoke(config, {"input": "hi"})

    # urllib normalizes header names to capitalized form.
    assert captured["headers"].get("X-api-key") == "s3cret"
    assert "Authorization" not in captured["headers"]
    assert captured["body"] == {"ask": "hi"}


def test_legacy_bearer_auth_default(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ENV", "tok123")
    payload = json.dumps({"text": "ok"}).encode("utf-8")
    captured: Dict[str, Any] = {}
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload), captured)

    config = _config(auth_header_env="TOKEN_ENV")
    _invoke(config, {"input": "hi"})

    assert captured["headers"].get("Authorization") == "Bearer tok123"


def test_custom_auth_header_on_streaming_target(monkeypatch) -> None:
    monkeypatch.setenv("ORCH_KEY", "s3cret")
    captured: Dict[str, Any] = {}
    _patch_urlopen(
        monkeypatch, _FakeStreamResponse([b"abc Hello"]), captured
    )

    config = _config(
        request_field="ask",
        response_mode="text",
        stream=StreamConfig(strip_leading_token=True),
        auth_header_env="ORCH_KEY",
        auth_header_name="X-API-KEY",
        auth_value_template="{token}",
    )
    result = _invoke(config, {"input": "hi"})

    assert result.response == "Hello"
    assert captured["headers"].get("X-api-key") == "s3cret"


# ---------------------------------------------------------------------------
# (e) response_fields: capture extra named fields from a json response
# ---------------------------------------------------------------------------


def test_response_fields_capture_dotted_paths(monkeypatch) -> None:
    payload = json.dumps(
        {
            "answer": "Paris is the capital of France.",
            "context": "France is a country in Europe. Its capital is Paris.",
            "retrieval": {
                "documents": [
                    {"id": "doc-1", "score": 0.91},
                    {"id": "doc-2", "score": 0.42},
                ]
            },
        }
    ).encode("utf-8")
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload))

    config = _config(
        request_field="ask",
        response_field="answer",
        response_fields={
            "context": "context",
            "retrieved_documents": "retrieval.documents",
        },
    )
    result = _invoke(config, {"input": "capital of France?"})

    assert result.response == "Paris is the capital of France."
    captured = result.metadata["response_fields"]
    assert captured["context"].startswith("France is a country")
    assert captured["retrieved_documents"] == [
        {"id": "doc-1", "score": 0.91},
        {"id": "doc-2", "score": 0.42},
    ]


def test_response_fields_missing_path_is_skipped(monkeypatch) -> None:
    payload = json.dumps({"answer": "ok", "context": "ctx"}).encode("utf-8")
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload))

    config = _config(
        response_field="answer",
        response_fields={"context": "context", "missing": "not.there"},
    )
    result = _invoke(config, {"input": "x"})

    captured = result.metadata["response_fields"]
    assert captured == {"context": "ctx"}


def test_no_response_fields_leaves_metadata_empty(monkeypatch) -> None:
    payload = json.dumps({"text": "echo: hi"}).encode("utf-8")
    _patch_urlopen(monkeypatch, _FakeJsonResponse(payload))

    config = _config()  # no response_fields configured
    result = _invoke(config, {"input": "hi"})

    assert result.response == "echo: hi"
    assert result.metadata == {}


def test_response_fields_rejected_on_non_http_target() -> None:
    with pytest.raises(ValueError, match="only valid for"):
        AgentOpsConfig(
            version=1,
            agent="my-prompt-agent:3",
            dataset="./qa.jsonl",
            response_fields={"context": "context"},
        )
