"""Unit tests for the conversation-building helper used by agent evaluators.

When the dataset row carries ``tool_calls``, AgentOps upgrades the plain
``query`` + ``response`` strings into a conversational message list so
evaluators like ``IntentResolutionEvaluator`` and ``TaskAdherenceEvaluator``
can see the agent's tool_call + tool_result trace and grade it accurately.
"""

from __future__ import annotations

from agentops.pipeline.runtime import _build_conversation_messages
from agentops.pipeline import runtime


def test_builds_text_only_conversation_when_no_tool_calls() -> None:
    """Even without tool calls, we build a structured conversation so the
    Azure evaluators don't try to parse plain query strings as history and
    emit ``WARNING: Conversation history could not be parsed``.
    """
    out = _build_conversation_messages(
        input_text="Hi", response_text="Hello.", tool_calls=None,
    )
    assert out is not None
    assert out["query"] == [
        {"role": "user", "content": [{"type": "text", "text": "Hi"}]}
    ]
    assert out["response"] == [
        {"role": "assistant", "content": [{"type": "text", "text": "Hello."}]}
    ]

    out_empty = _build_conversation_messages(
        input_text="Hi", response_text="Hello.", tool_calls=[],
    )
    assert out_empty == out


def test_returns_none_when_no_response_and_no_tool_calls() -> None:
    """Nothing to evaluate — caller should fall back to plain kwargs."""
    assert _build_conversation_messages(
        input_text="Hi", response_text="", tool_calls=None,
    ) is None


def test_builds_user_assistant_messages_for_simple_call() -> None:
    out = _build_conversation_messages(
        input_text="Weather in Paris?",
        response_text="It's sunny in Paris.",
        tool_calls=[{
            "type": "function_call",
            "name": "get_weather",
            "arguments": {"location": "Paris"},
        }],
    )
    assert out is not None
    assert out["query"] == [
        {"role": "user", "content": [{"type": "text", "text": "Weather in Paris?"}]}
    ]
    # Two response messages: tool_call + final assistant text.
    assert len(out["response"]) == 2
    tool_call_msg = out["response"][0]
    assert tool_call_msg["role"] == "assistant"
    assert tool_call_msg["content"][0]["type"] == "tool_call"
    assert tool_call_msg["content"][0]["name"] == "get_weather"
    assert tool_call_msg["content"][0]["arguments"] == {"location": "Paris"}
    final = out["response"][-1]
    assert final == {
        "role": "assistant",
        "content": [{"type": "text", "text": "It's sunny in Paris."}],
    }


def test_includes_tool_result_when_provided() -> None:
    out = _build_conversation_messages(
        input_text="Weather?",
        response_text="It's 20C.",
        tool_calls=[{
            "name": "get_weather",
            "arguments": {"location": "Tokyo"},
            "result": "20C and clear",
        }],
    )
    assert out is not None
    # tool_call -> tool_result -> assistant final
    assert len(out["response"]) == 3
    assert out["response"][1]["role"] == "tool"
    assert out["response"][1]["content"][0]["tool_result"] == "20C and clear"


def test_parses_json_string_arguments() -> None:
    out = _build_conversation_messages(
        input_text="?",
        response_text="ok",
        tool_calls=[{
            "name": "f",
            "arguments": '{"x": 1}',
        }],
    )
    assert out is not None
    args = out["response"][0]["content"][0]["arguments"]
    assert args == {"x": 1}


def test_normalises_nested_function_envelope() -> None:
    # Foundry tool calls sometimes nest name/arguments under a ``function`` key.
    out = _build_conversation_messages(
        input_text="?",
        response_text="ok",
        tool_calls=[{
            "id": "call_123",
            "type": "function",
            "function": {"name": "lookup", "arguments": {"q": "x"}},
        }],
    )
    assert out is not None
    call = out["response"][0]["content"][0]
    assert call["name"] == "lookup"
    assert call["arguments"] == {"q": "x"}
    assert call["tool_call_id"] == "call_123"


def test_skips_calls_without_a_name() -> None:
    out = _build_conversation_messages(
        input_text="?",
        response_text="ok",
        tool_calls=[
            {"arguments": {"x": 1}},  # no name -> skipped
            {"name": "f"},
        ],
    )
    assert out is not None
    # Only the named call survives, plus the final assistant text.
    assert len(out["response"]) == 2
    assert out["response"][0]["content"][0]["name"] == "f"


def test_resolves_retrieval_and_telemetry_placeholders() -> None:
    resolved = runtime._resolve_kwargs(
        {
            "context": "$retrieved_context",
            "items": "$retrieved_context_items",
            "trace": "$telemetry.trace_id",
            "json_text": "$response.raw",
        },
        row={
            "input": "q",
            "retrieved_context": "doc text",
            "retrieved_context_items": [{"id": "doc1"}],
            "telemetry": {"trace_id": "trace-123"},
            "response": {"raw": "nested response"},
        },
        response="answer",
    )

    assert resolved["context"] == "doc text"
    assert resolved["items"] == [{"id": "doc1"}]
    assert resolved["trace"] == "trace-123"
    assert resolved["json_text"] == "nested response"
