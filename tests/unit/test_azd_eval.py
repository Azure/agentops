"""Tests for azd eval recipe discovery and metric binding."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentops.core.azd_eval import (
    AzdEvalRecipeAmbiguous,
    bind_threshold_metrics,
    find_eval_yaml,
    load_eval_recipe,
    metric_aliases,
    recipe_metric_names,
)


def _write_recipe(path: Path, *, evaluator: str = "builtin.coherence") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
name: travel-agent-eval
agent:
  name: travel-agent
  kind: prompt-agent
  version: 3
dataset_reference:
  name: smoke
  version: 7
  local_uri: datasets/smoke.jsonl
evaluators:
  - name: {evaluator}
    version: 1
  - booking_accuracy
options:
  eval_model: gpt-4o-mini
  max_samples: 5
x-preview-field: keep-me
""".lstrip(),
        encoding="utf-8",
    )


def test_find_eval_yaml_at_workspace_root(tmp_path: Path) -> None:
    recipe = tmp_path / "eval.yaml"
    _write_recipe(recipe)

    assert find_eval_yaml(tmp_path) == recipe.resolve()


def test_find_eval_yaml_under_src_agent(tmp_path: Path) -> None:
    recipe = tmp_path / "src" / "travel-agent" / "eval.yaml"
    _write_recipe(recipe)

    assert find_eval_yaml(tmp_path) == recipe.resolve()


def test_find_eval_yaml_requires_explicit_path_when_ambiguous(tmp_path: Path) -> None:
    _write_recipe(tmp_path / "eval.yaml")
    _write_recipe(tmp_path / "src" / "travel-agent" / "eval.yaml")

    with pytest.raises(AzdEvalRecipeAmbiguous, match="multiple"):
        find_eval_yaml(tmp_path)


def test_find_eval_yaml_resolves_explicit_relative_path(tmp_path: Path) -> None:
    recipe = tmp_path / "src" / "travel-agent" / "eval.yaml"
    _write_recipe(recipe)

    assert find_eval_yaml(tmp_path, Path("src/travel-agent/eval.yaml")) == recipe.resolve()


def test_load_eval_recipe_preserves_versions_as_strings(tmp_path: Path) -> None:
    recipe_path = tmp_path / "eval.yaml"
    _write_recipe(recipe_path)

    recipe = load_eval_recipe(recipe_path)

    assert recipe.agent is not None
    assert recipe.agent.version == "3"
    assert recipe.dataset_reference is not None
    assert recipe.dataset_reference.version == "7"
    assert recipe.evaluators[0].version == "1"
    assert recipe_metric_names(recipe) == {"builtin.coherence", "booking_accuracy"}


def test_recipe_metric_names_include_rubric_dimensions(tmp_path: Path) -> None:
    recipe_path = tmp_path / "eval.yaml"
    recipe_path.write_text(
        """
name: rubric-eval
agent:
  name: travel-agent
  kind: prompt-agent
evaluators:
  - name: travel_quality_rubric
    kind: rubric
    local_uri: evaluators/travel-quality.yaml
    eval_model: gpt-5.4-mini
    dimensions:
      - id: booking_accuracy
        description: Books the requested trip correctly.
        weight: 0.7
      - name: policy_enforcement
        description: Applies travel policy restrictions.
        weight: 0.3
""".lstrip(),
        encoding="utf-8",
    )

    recipe = load_eval_recipe(recipe_path)

    assert recipe.evaluators[0].local_uri == "evaluators/travel-quality.yaml"
    assert recipe.evaluators[0].eval_model == "gpt-5.4-mini"
    assert recipe_metric_names(recipe) == {
        "travel_quality_rubric",
        "booking_accuracy",
        "policy_enforcement",
    }


def test_metric_aliases_are_narrow_and_include_similarity_compat() -> None:
    assert metric_aliases("builtin.text_similarity") == (
        "builtin.text_similarity",
        "similarity",
        "text_similarity",
    )
    assert metric_aliases("booking_accuracy") == ("booking_accuracy",)


def test_bind_threshold_metrics_maps_builtin_suffix_and_custom_literal() -> None:
    binding = bind_threshold_metrics(
        ["coherence", "similarity", "booking_accuracy"],
        ["builtin.coherence", "builtin.text_similarity", "booking_accuracy"],
    )

    assert binding.ok
    assert binding.bound == {
        "coherence": "builtin.coherence",
        "similarity": "builtin.text_similarity",
        "booking_accuracy": "booking_accuracy",
    }


def test_bind_threshold_metrics_fails_closed_for_unmatched_thresholds() -> None:
    binding = bind_threshold_metrics(
        ["groundedness"],
        ["builtin.coherence"],
    )

    assert not binding.ok
    assert binding.unmatched == ("groundedness",)
    assert binding.bound == {}
