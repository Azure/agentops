"""Tests for the evaluator catalog and auto-selection rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.agentops_config import Threshold, classify_agent
from agentops.core.evaluators import (
    CATALOG,
    DatasetShape,
    detect_dataset_shape,
    merge_thresholds,
    select_evaluators,
)


# ---------------------------------------------------------------------------
# detect_dataset_shape
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    import json

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class TestDetectDatasetShape:
    def test_quality_dataset(self, tmp_path: Path) -> None:
        path = tmp_path / "qa.jsonl"
        _write_jsonl(
            path,
            [
                {"input": "hello", "expected": "hi"},
                {"input": "bye", "expected": "goodbye"},
            ],
        )
        shape = detect_dataset_shape(path)
        assert shape.row_count == 2
        assert not shape.looks_rag
        assert not shape.looks_tool_use

    def test_rag_dataset(self, tmp_path: Path) -> None:
        path = tmp_path / "rag.jsonl"
        _write_jsonl(
            path,
            [
                {"input": "q", "expected": "a", "context": "Paris is the capital."},
            ],
        )
        shape = detect_dataset_shape(path)
        assert shape.looks_rag

    def test_tool_use_dataset(self, tmp_path: Path) -> None:
        path = tmp_path / "tools.jsonl"
        _write_jsonl(
            path,
            [
                {
                    "input": "weather?",
                    "expected": "sunny",
                    "tool_calls": [{"name": "get_weather", "args": {}}],
                },
            ],
        )
        shape = detect_dataset_shape(path)
        assert shape.looks_tool_use

    def test_empty_context_does_not_count(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_ctx.jsonl"
        _write_jsonl(
            path,
            [{"input": "q", "expected": "a", "context": ""}],
        )
        shape = detect_dataset_shape(path)
        assert not shape.looks_rag

    def test_empty_dataset_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            detect_dataset_shape(path)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            detect_dataset_shape(path)

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            detect_dataset_shape(tmp_path / "missing.jsonl")


# ---------------------------------------------------------------------------
# select_evaluators
# ---------------------------------------------------------------------------


_PROMPT_AGENT = classify_agent("my-rag:3")
_MODEL_DIRECT = classify_agent("model:gpt-4o")
_HTTP_AGENT = classify_agent("https://my-app.azurecontainerapps.io/chat")


def _shape(*, context: bool = False, tool_calls: bool = False, tool_defs: bool = False) -> DatasetShape:
    return DatasetShape(
        has_context=context,
        has_tool_calls=tool_calls,
        has_tool_definitions=tool_defs,
        row_count=10,
    )


class TestSelectEvaluators:
    def test_quality_baseline_always_present(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape())
        names = [p.name for p in result]
        assert "CoherenceEvaluator" in names
        assert "FluencyEvaluator" in names
        assert "SimilarityEvaluator" in names
        assert "ResponseCompletenessEvaluator" in names
        assert "F1ScoreEvaluator" not in names
        assert "avg_latency_seconds" in names

    def test_quality_only_for_quality_dataset(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape())
        names = [p.name for p in result]
        assert "GroundednessEvaluator" not in names
        assert "ToolCallAccuracyEvaluator" not in names

    def test_rag_evaluators_added_with_context(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape(context=True))
        names = [p.name for p in result]
        for evaluator in [
            "GroundednessEvaluator",
            "RelevanceEvaluator",
            "RetrievalEvaluator",
            "ResponseCompletenessEvaluator",
        ]:
            assert evaluator in names

    def test_tool_use_added_with_tool_calls(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape(tool_calls=True))
        names = [p.name for p in result]
        assert "ToolCallAccuracyEvaluator" in names
        assert "ToolCallAccuracyEvaluator" in names

    def test_tool_use_added_with_tool_definitions(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape(tool_defs=True))
        names = [p.name for p in result]
        assert "ToolCallAccuracyEvaluator" in names

    def test_combined_rag_and_tools(self) -> None:
        result = select_evaluators(_PROMPT_AGENT, _shape(context=True, tool_calls=True))
        names = [p.name for p in result]
        assert "GroundednessEvaluator" in names
        assert "ToolCallAccuracyEvaluator" in names

    def test_model_direct_skips_agent_evaluators(self) -> None:
        # Even if the dataset has context/tool_calls, model targets stay quality-only.
        result = select_evaluators(
            _MODEL_DIRECT, _shape(context=True, tool_calls=True)
        )
        names = [p.name for p in result]
        assert "GroundednessEvaluator" not in names
        assert "ToolCallAccuracyEvaluator" not in names
        assert "CoherenceEvaluator" in names
        assert "SimilarityEvaluator" in names
        assert "F1ScoreEvaluator" in names
        assert "ResponseCompletenessEvaluator" not in names

    def test_http_agent_treated_like_agent(self) -> None:
        result = select_evaluators(_HTTP_AGENT, _shape(context=True))
        names = [p.name for p in result]
        assert "GroundednessEvaluator" in names

    def test_overrides_bypass_inference(self) -> None:
        result = select_evaluators(
            _PROMPT_AGENT,
            _shape(context=True, tool_calls=True),
            overrides=["CoherenceEvaluator"],
            threshold_metrics=["f1_score"],
        )
        names = [p.name for p in result]
        assert names == ["CoherenceEvaluator"]

    def test_threshold_metric_opts_into_optional_quality_evaluator(self) -> None:
        result = select_evaluators(
            _PROMPT_AGENT,
            _shape(),
            threshold_metrics=["f1_score"],
        )
        names = [p.name for p in result]
        assert "F1ScoreEvaluator" in names

    def test_unknown_override_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown evaluator"):
            select_evaluators(_PROMPT_AGENT, _shape(), overrides=["NotAnEvaluator"])


# ---------------------------------------------------------------------------
# merge_thresholds
# ---------------------------------------------------------------------------


class TestMergeThresholds:
    def test_user_override_wins(self) -> None:
        presets = select_evaluators(_PROMPT_AGENT, _shape())
        user = [Threshold(metric="coherence", criteria=">=", value=4.0)]
        merged = merge_thresholds(presets, user)
        coherence = [t for t in merged if t.metric == "coherence"][0]
        assert coherence.value == 4.0

    def test_preset_default_used_when_no_override(self) -> None:
        presets = select_evaluators(_PROMPT_AGENT, _shape())
        merged = merge_thresholds(presets, user_thresholds=[])
        # CoherenceEvaluator default is >=3.0
        coherence = [t for t in merged if t.metric == "coherence"][0]
        assert coherence.value == 3.0

    def test_user_only_metric_appended(self) -> None:
        presets = select_evaluators(_PROMPT_AGENT, _shape())
        user = [Threshold(metric="custom_metric", criteria=">=", value=1.0)]
        merged = merge_thresholds(presets, user)
        names = [t.metric for t in merged]
        assert "custom_metric" in names


# ---------------------------------------------------------------------------
# CATALOG
# ---------------------------------------------------------------------------


def test_catalog_keys_match_preset_names() -> None:
    for name, preset in CATALOG.items():
        assert preset.name == name
