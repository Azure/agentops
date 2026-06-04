"""Pure helpers for Azure Developer CLI AI agent evaluation recipes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agentops.utils.yaml import load_yaml


EVAL_RECIPE_FILENAMES = ("eval.yaml", "eval.yml")


class AzdEvalRecipeError(ValueError):
    """Raised when an azd evaluation recipe cannot be discovered or parsed."""


class AzdEvalRecipeAmbiguous(AzdEvalRecipeError):
    """Raised when multiple azd evaluation recipes exist and none was selected."""


class EvalAgent(BaseModel):
    """Agent section from an azd ``eval.yaml`` recipe."""

    name: Optional[str] = None
    kind: Optional[str] = None
    version: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class EvalDatasetReference(BaseModel):
    """Dataset reference section from an azd ``eval.yaml`` recipe."""

    name: Optional[str] = None
    version: Optional[str] = None
    local_uri: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class EvalRubricDimension(BaseModel):
    """Rubric dimension declared by an azd/Foundry rubric evaluator."""

    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    weight: Optional[float] = None
    always_applicable: Optional[bool] = None

    model_config = ConfigDict(extra="allow")

    @property
    def metric_name(self) -> Optional[str]:
        """Return the stable metric key for this rubric dimension."""

        for value in (self.id, self.name):
            if value and value.strip():
                return value.strip()
        return None


class EvalEvaluator(BaseModel):
    """Evaluator entry from an azd ``eval.yaml`` recipe."""

    name: str
    version: Optional[str] = None
    kind: Optional[str] = None
    dimensions: list[EvalRubricDimension] = Field(default_factory=list)
    local_uri: Optional[str] = None
    eval_model: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"name": data}
        if isinstance(data, dict):
            for key in ("name", "id", "evaluator", "metric"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    coerced = dict(data)
                    coerced["name"] = value.strip()
                    return coerced
        return data

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("azd eval evaluator name must be non-empty")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class EvalOptions(BaseModel):
    """Options section from an azd ``eval.yaml`` recipe."""

    eval_model: Optional[str] = None
    max_samples: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class EvalRecipe(BaseModel):
    """Tolerant model for azd ``eval.yaml``.

    Unknown fields are preserved for forward compatibility with preview azd
    schemas, while core fields still validate strictly enough for AgentOps
    routing and threshold binding.
    """

    name: Optional[str] = None
    agent: Optional[EvalAgent] = None
    dataset_reference: Optional[EvalDatasetReference] = None
    evaluators: list[EvalEvaluator] = Field(default_factory=list)
    options: Optional[EvalOptions] = None

    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True)
class MetricBinding:
    """Mapping from user threshold names to actual azd metric names."""

    bound: Dict[str, str]
    unmatched: tuple[str, ...]
    ambiguous: Dict[str, tuple[str, ...]]
    unused_metrics: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.unmatched and not self.ambiguous


_BUILTIN_ALIASES: dict[str, tuple[str, ...]] = {
    "builtin.coherence": ("coherence",),
    "builtin.fluency": ("fluency",),
    "builtin.text_similarity": ("similarity", "text_similarity"),
    "builtin.f1_score": ("f1_score", "f1"),
    "builtin.groundedness": ("groundedness",),
    "builtin.relevance": ("relevance",),
    "builtin.retrieval": ("retrieval",),
    "builtin.response_completeness": ("response_completeness",),
    "builtin.tool_call_accuracy": ("tool_call_accuracy",),
    "builtin.intent_resolution": ("intent_resolution",),
    "builtin.task_adherence": ("task_adherence",),
    "builtin.tool_selection": ("tool_selection",),
    "builtin.tool_input_accuracy": ("tool_input_accuracy",),
    "builtin.task_completion": ("task_completion",),
}


def find_eval_yaml(workspace: Path, explicit_path: Optional[Path] = None) -> Optional[Path]:
    """Find an azd ``eval.yaml`` recipe under ``workspace``.

    Discovery is deterministic. A single recipe can live at the workspace root
    or under ``src/<agent>/``. Multiple candidates require an explicit
    ``eval_recipe`` path.
    """

    root = workspace.resolve()
    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else root / explicit_path
        return path.resolve()

    candidates: list[Path] = []
    for filename in EVAL_RECIPE_FILENAMES:
        candidate = root / filename
        if candidate.exists():
            candidates.append(candidate)

    src_dir = root / "src"
    if src_dir.exists():
        for child in sorted(src_dir.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            for filename in EVAL_RECIPE_FILENAMES:
                candidate = child / filename
                if candidate.exists():
                    candidates.append(candidate)

    unique = sorted({candidate.resolve() for candidate in candidates})
    if not unique:
        return None
    if len(unique) > 1:
        display = ", ".join(str(path.relative_to(root)) for path in unique)
        raise AzdEvalRecipeAmbiguous(
            "multiple azd eval recipes found; set 'eval_recipe' in agentops.yaml "
            f"to choose one: {display}"
        )
    return unique[0]


def load_eval_recipe(path: Path) -> EvalRecipe:
    """Load and validate an azd ``eval.yaml`` recipe."""

    try:
        data = load_yaml(path)
        return EvalRecipe.model_validate(data)
    except ValidationError as exc:
        raise AzdEvalRecipeError(f"invalid azd eval recipe {path}: {exc}") from exc


def recipe_metric_names(recipe: EvalRecipe) -> set[str]:
    """Return raw metric names declared by an azd recipe."""

    names: set[str] = set()
    for evaluator in recipe.evaluators:
        names.add(evaluator.name)
        for dimension in evaluator.dimensions:
            dimension_name = dimension.metric_name
            if dimension_name:
                names.add(dimension_name)
    return names


def metric_aliases(metric_name: str) -> tuple[str, ...]:
    """Return supported threshold aliases for an azd metric name."""

    raw = metric_name.strip()
    if not raw:
        return ()

    aliases: list[str] = [raw]
    aliases.extend(_BUILTIN_ALIASES.get(raw, ()))

    if raw.startswith("builtin."):
        suffix = raw.removeprefix("builtin.")
        aliases.append(suffix)
    elif "." in raw:
        aliases.append(raw.rsplit(".", 1)[-1])

    seen: set[str] = set()
    deduped: list[str] = []
    for alias in aliases:
        if alias and alias not in seen:
            seen.add(alias)
            deduped.append(alias)
    return tuple(deduped)


def bind_threshold_metrics(
    threshold_names: Iterable[str],
    available_metrics: Iterable[str],
) -> MetricBinding:
    """Bind user threshold keys to actual azd metric names.

    Exact names win first. Builtin aliases are intentionally narrow to avoid
    broad fuzzy matching that could create false-green gates.
    """

    metrics = tuple(metric for metric in available_metrics if metric)
    alias_to_metrics: dict[str, list[str]] = {}
    for metric in metrics:
        for alias in metric_aliases(metric):
            alias_to_metrics.setdefault(alias, []).append(metric)

    bound: Dict[str, str] = {}
    unmatched: list[str] = []
    ambiguous: Dict[str, tuple[str, ...]] = {}

    for threshold in threshold_names:
        matches = alias_to_metrics.get(threshold, [])
        if not matches:
            unmatched.append(threshold)
            continue
        unique_matches = tuple(dict.fromkeys(matches))
        if len(unique_matches) > 1:
            ambiguous[threshold] = unique_matches
            continue
        bound[threshold] = unique_matches[0]

    used = set(bound.values())
    unused = tuple(metric for metric in metrics if metric not in used)
    return MetricBinding(
        bound=bound,
        unmatched=tuple(unmatched),
        ambiguous=ambiguous,
        unused_metrics=unused,
    )
