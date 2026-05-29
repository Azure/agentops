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


def test_extracts_score_zero_as_legitimate_value():
    """``score: 0`` is the lowest valid number and must not be coerced to None.
    Real Foundry safety graders (violence/sexual/self_harm) emit ``score: 0``
    on a clean row plus ``label: "pass"``; treating zero as missing collapses
    the row to ``missing`` in the threshold table."""
    items = [
        _item(
            {"input": "hi"},
            {"output_text": "hello"},
            [{"name": "violence", "score": 0, "label": "pass", "passed": True}],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    by_name = {m.name: m.value for m in rows[0].metrics}
    assert by_name == {"violence": 0.0}


def test_extracts_real_foundry_azure_ai_evaluator_result_shape():
    """The on-the-wire shape emitted by Foundry's ``azure_ai_evaluator``
    grader carries both ``metric`` and ``name`` plus a ``label``, a
    ``threshold``, and a ``passed`` boolean. The parser must find the
    score under the canonical ``score`` field even with all the extra keys
    present (extras must not shadow the score). Schema sourced from
    Azure/azure-sdk-for-python evaluation fixture
    ``evaluation_util_convert_expected_output.json``."""
    items = [
        _item(
            {"input": "hi", "expected": "hello"},
            {"output_text": "hello"},
            [
                {
                    "type": "azure_ai_evaluator",
                    "name": "violence",
                    "metric": "violence",
                    "score": 0,
                    "label": "pass",
                    "reason": "no violent content detected",
                    "threshold": 3,
                    "passed": True,
                    "sample": {"output_text": "hello"},
                    "status": "completed",
                },
                {
                    "type": "azure_ai_evaluator",
                    "name": "coherence",
                    "metric": "coherence",
                    "score": 4.5,
                    "reason": "well-structured response",
                    "passed": True,
                    "status": "completed",
                },
            ],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    by_name = {m.name: m.value for m in rows[0].metrics}
    assert by_name == {"violence": 0.0, "coherence": 4.5}


def test_extracts_score_nested_in_sample_when_top_level_missing():
    """Some custom Foundry prompt-based graders only populate
    ``result["sample"]["score"]`` rather than ``result["score"]``. The
    parser must descend into ``sample`` as a fallback so those metrics
    don't show up as missing."""
    items = [
        _item(
            {"input": "hi"},
            {"output_text": "hello"},
            [{"name": "custom_quality", "sample": {"score": 3.5}}],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    by_name = {m.name: m.value for m in rows[0].metrics}
    assert by_name == {"custom_quality": 3.5}


def test_extracts_score_from_label_when_no_numeric_score():
    """Binary content-safety graders sometimes return only ``label: pass``
    / ``label: fail`` with no numeric score. Treat those as 1.0 / 0.0 so
    they don't drop out of the threshold table as missing."""
    items = [
        _item(
            {"input": "hi"},
            {"output_text": "hello"},
            [
                {"name": "protected_material", "label": "pass"},
                {"name": "hate_unfairness", "label": "fail"},
            ],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    by_name = {m.name: m.value for m in rows[0].metrics}
    assert by_name == {"protected_material": 1.0, "hate_unfairness": 0.0}


def test_records_diagnostic_reason_when_score_is_missing():
    """When a grader returns absolutely no usable score the parser emits a
    structured ``error`` pointing operators at the raw items file. Silent
    nulls were the symptom that motivated this fix."""
    items = [
        _item(
            {"input": "hi"},
            {"output_text": "hello"},
            [{"name": "coherence"}],
        ),
    ]
    rows = rows_from_cloud_output_items(items)
    metric = rows[0].metrics[0]
    assert metric.value is None
    assert metric.error is not None
    assert "cloud_output_items.json" in metric.error
