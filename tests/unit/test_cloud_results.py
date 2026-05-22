"""Tests for :mod:`agentops.pipeline.cloud_results`."""

from __future__ import annotations

import json

from agentops.pipeline.cloud_results import rows_from_cloud_output_items


def _item(datasource, sample, results):
    return {"datasource_item": datasource, "sample": sample, "results": results}


def test_extracts_text_from_output_items_list():
    """sample.output as a flat list of {text, type} dicts (Foundry shape)."""
    items = [
        _item(
            {"input": "hi", "expected": "hello"},
            {
                "output": [
                    {
                        "annotations": [],
                        "text": "hello",
                        "type": "output_text",
                        "logprobs": [],
                    }
                ]
            },
            [{"name": "similarity", "score": 5.0}],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    assert rows[0].response == "hello"


def test_extracts_text_from_responses_api_content_blocks():
    """sample.output[i].content as a list of {type, text} blocks
    (OpenAI Responses API canonical shape)."""
    items = [
        _item(
            {"input": "hi"},
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Paris is the capital."}
                        ],
                    }
                ]
            },
            [],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    assert rows[0].response == "Paris is the capital."


def test_reparses_json_encoded_output_text():
    """When Foundry stores a JSON-stringified output_items list under
    ``output_text``, we should reparse it instead of passing the JSON
    through as the response."""
    payload = json.dumps(
        [
            {
                "annotations": [],
                "text": "Paris is the capital of France.",
                "type": "output_text",
                "logprobs": [],
            }
        ]
    )
    items = [_item({"input": "hi"}, {"output_text": payload}, [])]
    rows = rows_from_cloud_output_items(items)
    assert rows[0].response == "Paris is the capital of France."


def test_falls_back_to_empty_string_when_sample_unrecognized():
    items = [_item({"input": "hi"}, {"strange_field": 42}, [])]
    rows = rows_from_cloud_output_items(items)
    assert rows[0].response == ""


def test_extracts_metric_scores_from_results():
    items = [
        _item(
            {"input": "hi", "expected": "hello"},
            {"output_text": "hello"},
            [
                {"name": "similarity", "score": 5.0},
                {"name": "coherence", "score": 4},
                {"name": "f1_score", "value": 1.0},
                {"name": "fluency", "passed": True},
            ],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    by_name = {m.name: m.value for m in rows[0].metrics}
    assert by_name == {
        "similarity": 5.0,
        "coherence": 4.0,
        "f1_score": 1.0,
        "fluency": 1.0,
    }


def test_passes_through_context_and_tool_calls_from_datasource():
    items = [
        _item(
            {
                "input": "hi",
                "expected": "hello",
                "context": "greeting context",
                "tool_calls": [{"name": "lookup"}],
            },
            {"output_text": "hello"},
            [],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    assert rows[0].context == "greeting context"
    assert rows[0].tool_calls == [{"name": "lookup"}]
