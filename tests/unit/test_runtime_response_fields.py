"""Unit tests for the ``$response.<name>`` evaluator placeholder.

The grey-box RAG evaluators need to score the live context an http-json
target returns alongside the answer on the same call. ``_resolve_kwargs``
exposes those captured fields through the ``$response.<name>`` token so an
evaluator's ``input_mapping`` can wire ``context: $response.context`` to the
value captured by the target's ``response_fields`` map.
"""

from __future__ import annotations

import pytest

from agentops.pipeline.runtime import _resolve_kwargs


def test_response_token_resolves_from_captured_fields() -> None:
    out = _resolve_kwargs(
        {"response": "$prediction", "context": "$response.context"},
        row={"input": "capital of France?"},
        response="Paris.",
        response_fields={"context": "France's capital is Paris."},
    )
    assert out == {"response": "Paris.", "context": "France's capital is Paris."}


def test_response_token_missing_capture_is_skipped() -> None:
    # No captured value -> the kwarg is omitted rather than passing None,
    # mirroring how unresolved dataset placeholders behave.
    out = _resolve_kwargs(
        {"context": "$response.context"},
        row={"input": "x"},
        response="ans",
        response_fields={},
    )
    assert out == {}


def test_response_token_for_arbitrary_field_name() -> None:
    docs = [{"id": "doc-1", "score": 0.9}]
    out = _resolve_kwargs(
        {"retrieval_ground_truth": "$row.qrels",
         "retrieved_documents": "$response.retrieved_documents"},
        row={"input": "q", "qrels": {"doc-1": 1}},
        response="ans",
        response_fields={"retrieved_documents": docs},
    )
    assert out["retrieved_documents"] == docs
    assert out["retrieval_ground_truth"] == {"doc-1": 1}


def test_row_token_missing_field_is_skipped() -> None:
    out = _resolve_kwargs(
        {"qrels": "$row.qrels"},
        row={"input": "x"},
        response="ans",
    )
    assert out == {}


def test_builtin_context_token_unchanged_without_capture() -> None:
    # Backward compat: $context still resolves from the dataset row when no
    # response_fields are provided.
    out = _resolve_kwargs(
        {"context": "$context"},
        row={"input": "x", "context": "dataset context"},
        response="ans",
    )
    assert out == {"context": "dataset context"}


def test_unknown_placeholder_still_raises() -> None:
    with pytest.raises(ValueError, match="unknown evaluator placeholder"):
        _resolve_kwargs(
            {"x": "$nope"},
            row={"input": "x"},
            response="ans",
            response_fields={"context": "c"},
        )
