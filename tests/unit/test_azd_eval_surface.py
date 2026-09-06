"""Tests for cross-surface azd recipe classification, discovery, and parsing.

Covers quickstart scenario T1.2. Kept in a separate module from
``test_azd_eval.py`` so the pre-existing legacy tests there stay untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentops.core.azd_eval import (
    AzdEvalRecipeAmbiguous,
    AzdEvalRecipeError,
    EvalSurface,
    classify_recipe_file,
    current_recipe_dataset_path,
    current_recipe_metric_names,
    load_current_eval_recipe,
    resolve_recipe,
)


LEGACY_RECIPE = """
name: travel-agent-eval
agent:
  name: travel-agent
  kind: prompt-agent
  version: 3
dataset_reference:
  name: smoke
  version: 1
  local_uri: datasets/smoke.jsonl
evaluators:
  - name: builtin.coherence
""".lstrip()

CURRENT_RECIPE = """
datasets:
  - name: support-agent-regression
    file: ./datasets/support-agent-regression.jsonl

evaluators:
  - name: support-agent-quality
    source: ./evaluators/support-agent-quality.json

evals:
  - name: support-agent-regression-eval
    dataset: support-agent-regression
    evaluation_level: turn
    max_samples: 50
    evaluators:
      - evaluator: builtin.task_adherence
        initialization_parameters:
          model: gpt-4o
      - evaluator: support-agent-quality
        version: 2
    target:
      type: agent
      name: support-agent
x-preview-field: keep-me
""".lstrip()


def _write_legacy(root: Path, relative: str = "eval.yaml") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LEGACY_RECIPE, encoding="utf-8")
    return path.resolve()


def _write_current(root: Path, *, with_rubric: bool = True) -> Path:
    path = root / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CURRENT_RECIPE, encoding="utf-8")
    if with_rubric:
        rubric = root / "evals" / "evaluators" / "support-agent-quality.json"
        rubric.parent.mkdir(parents=True, exist_ok=True)
        rubric.write_text(
            json.dumps(
                {
                    "dimensions": [
                        {"id": "accuracy", "description": "Factually correct.", "weight": 5},
                        {"id": "tone", "description": "Stays polite.", "weight": 2},
                    ]
                }
            ),
            encoding="utf-8",
        )
    return path.resolve()


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_current_recipe_by_evals_sequence(tmp_path: Path) -> None:
    path = _write_current(tmp_path)

    assert classify_recipe_file(path) is EvalSurface.CURRENT


def test_classify_legacy_recipe_by_agent_mapping(tmp_path: Path) -> None:
    path = _write_legacy(tmp_path)

    assert classify_recipe_file(path) is EvalSurface.LEGACY


def test_classification_prefers_content_over_filename(tmp_path: Path) -> None:
    """A legacy document parked at the current-surface path is still legacy."""

    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LEGACY_RECIPE, encoding="utf-8")

    assert classify_recipe_file(path) is EvalSurface.LEGACY


def test_classification_falls_back_to_location_hint_for_minimal_legacy(tmp_path: Path) -> None:
    """A minimal legacy recipe at a legacy location still classifies."""

    path = tmp_path / "eval.yaml"
    path.write_text("name: minimal\nevaluators:\n  - builtin.coherence\n", encoding="utf-8")

    assert classify_recipe_file(path) is EvalSurface.LEGACY


def test_unclassifiable_document_is_a_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "custom.yaml"
    path.write_text("something: else\n", encoding="utf-8")

    with pytest.raises(AzdEvalRecipeError, match="matches neither supported schema"):
        classify_recipe_file(path)


# ---------------------------------------------------------------------------
# Discovery precedence
# ---------------------------------------------------------------------------


def test_resolve_finds_current_recipe_only(tmp_path: Path) -> None:
    path = _write_current(tmp_path)

    resolution = resolve_recipe(tmp_path)

    assert resolution.path == path
    assert resolution.surface is EvalSurface.CURRENT
    assert resolution.skipped == ()
    assert resolution.explicit is False
    assert resolution.extension == "azure.ai.evaluations"


def test_resolve_finds_legacy_recipe_at_root(tmp_path: Path) -> None:
    path = _write_legacy(tmp_path)

    resolution = resolve_recipe(tmp_path)

    assert resolution.path == path
    assert resolution.surface is EvalSurface.LEGACY
    assert resolution.extension == "azure.ai.agents"


def test_resolve_finds_legacy_recipe_under_src_agent(tmp_path: Path) -> None:
    path = _write_legacy(tmp_path, "src/travel-agent/eval.yaml")

    resolution = resolve_recipe(tmp_path)

    assert resolution.path == path
    assert resolution.surface is EvalSurface.LEGACY


def test_current_surface_wins_and_reports_the_skipped_legacy_recipe(tmp_path: Path) -> None:
    legacy = _write_legacy(tmp_path)
    current = _write_current(tmp_path)

    resolution = resolve_recipe(tmp_path)

    assert resolution.path == current
    assert resolution.surface is EvalSurface.CURRENT
    assert resolution.skipped == (legacy,)


def test_two_legacy_candidates_are_ambiguous(tmp_path: Path) -> None:
    _write_legacy(tmp_path)
    _write_legacy(tmp_path, "src/travel-agent/eval.yaml")

    with pytest.raises(AzdEvalRecipeAmbiguous, match="multiple"):
        resolve_recipe(tmp_path)


def test_two_current_candidates_are_ambiguous(tmp_path: Path) -> None:
    _write_current(tmp_path)
    (tmp_path / "evals" / "azure.eval.yml").write_text(CURRENT_RECIPE, encoding="utf-8")

    with pytest.raises(AzdEvalRecipeAmbiguous, match="multiple"):
        resolve_recipe(tmp_path)


def test_explicit_recipe_bypasses_discovery(tmp_path: Path) -> None:
    _write_current(tmp_path)
    legacy = _write_legacy(tmp_path, "config/nightly/eval.yaml")

    resolution = resolve_recipe(tmp_path, Path("config/nightly/eval.yaml"))

    assert resolution.path == legacy
    assert resolution.surface is EvalSurface.LEGACY
    assert resolution.explicit is True
    assert resolution.skipped == ()


def test_missing_explicit_recipe_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(AzdEvalRecipeError, match="azd eval recipe not found at"):
        resolve_recipe(tmp_path, Path("nope/azure.eval.yaml"))


def test_no_recipe_names_both_supported_locations(tmp_path: Path) -> None:
    with pytest.raises(AzdEvalRecipeError) as excinfo:
        resolve_recipe(tmp_path)

    message = str(excinfo.value)
    assert "azd eval recipe not found" in message
    assert "evals/azure.eval.yaml" in message
    assert "eval.yaml" in message


# ---------------------------------------------------------------------------
# Current recipe parsing
# ---------------------------------------------------------------------------


def test_current_recipe_parses_dataset_file_key(tmp_path: Path) -> None:
    path = _write_current(tmp_path)

    recipe = load_current_eval_recipe(path)

    assert recipe.datasets[0].name == "support-agent-regression"
    assert recipe.datasets[0].file == "./datasets/support-agent-regression.jsonl"


def test_current_recipe_preserves_unknown_top_level_fields(tmp_path: Path) -> None:
    path = _write_current(tmp_path)

    recipe = load_current_eval_recipe(path)

    assert recipe.model_dump()["x-preview-field"] == "keep-me"


def test_current_recipe_coerces_versions_to_strings(tmp_path: Path) -> None:
    path = _write_current(tmp_path)

    recipe = load_current_eval_recipe(path)
    evaluation = recipe.primary_eval()

    assert evaluation.evaluators[1].version == "2"


def test_primary_eval_rejects_multiple_declared_evaluations(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
evals:
  - name: first
    evaluators:
      - evaluator: builtin.coherence
  - name: second
    evaluators:
      - evaluator: builtin.fluency
""".lstrip(),
        encoding="utf-8",
    )

    recipe = load_current_eval_recipe(path)

    with pytest.raises(AzdEvalRecipeAmbiguous, match="multiple evaluations"):
        recipe.primary_eval()


def test_primary_eval_rejects_recipe_with_no_evaluations(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("evals: []\n", encoding="utf-8")

    recipe = load_current_eval_recipe(path)

    with pytest.raises(AzdEvalRecipeError, match="declares no evaluations"):
        recipe.primary_eval()


# ---------------------------------------------------------------------------
# Declared metric names (pre-flight binding input)
# ---------------------------------------------------------------------------


def test_declared_metric_names_cover_builtins_labels_and_rubric_dimensions(
    tmp_path: Path,
) -> None:
    path = _write_current(tmp_path)
    recipe = load_current_eval_recipe(path)

    names = current_recipe_metric_names(recipe, path)

    assert names == {
        "builtin.task_adherence",
        "support-agent-quality",
        "accuracy",
        "tone",
    }


def test_declared_metric_names_use_the_reference_label_when_present(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
evals:
  - name: labelled
    evaluators:
      - evaluator: builtin.task_adherence
        name: adherence_strict
""".lstrip(),
        encoding="utf-8",
    )
    recipe = load_current_eval_recipe(path)

    assert current_recipe_metric_names(recipe, path) == {"adherence_strict"}


def test_declared_metric_names_read_inline_rubric_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
evaluators:
  - name: brevity
    definition:
      type: rubric
      dimensions:
        - id: length
          weight: 1
          description: Answers without restating the question.
evals:
  - name: inline-rubric
    evaluators:
      - evaluator: brevity
""".lstrip(),
        encoding="utf-8",
    )
    recipe = load_current_eval_recipe(path)

    assert current_recipe_metric_names(recipe, path) == {"brevity", "length"}


def test_missing_rubric_definition_is_a_configuration_error(tmp_path: Path) -> None:
    path = _write_current(tmp_path, with_rubric=False)
    recipe = load_current_eval_recipe(path)

    with pytest.raises(AzdEvalRecipeError, match="rubric definition that does not exist"):
        current_recipe_metric_names(recipe, path)


# ---------------------------------------------------------------------------
# Dataset provenance
# ---------------------------------------------------------------------------


def test_dataset_path_resolves_the_declared_local_file(tmp_path: Path) -> None:
    path = _write_current(tmp_path)
    recipe = load_current_eval_recipe(path)

    resolved = current_recipe_dataset_path(recipe, recipe.primary_eval(), path)

    assert resolved.endswith("support-agent-regression.jsonl")
    assert "evals" in resolved


def test_dataset_path_describes_a_trace_source(tmp_path: Path) -> None:
    path = tmp_path / "evals" / "azure.eval.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
evals:
  - name: trace-eval
    source:
      type: traces
      agent_name: support-agent
      lookback_hours: 24
      max_traces: 20
    evaluators:
      - evaluator: builtin.task_adherence
""".lstrip(),
        encoding="utf-8",
    )
    recipe = load_current_eval_recipe(path)

    resolved = current_recipe_dataset_path(recipe, recipe.primary_eval(), path)

    assert resolved.startswith("azd:traces")
    assert "agent=support-agent" in resolved
