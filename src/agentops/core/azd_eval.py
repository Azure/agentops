"""Pure helpers for Azure Developer CLI AI agent evaluation recipes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agentops.utils.yaml import load_yaml


EVAL_RECIPE_FILENAMES = ("eval.yaml", "eval.yml")

#: Default directory holding a current-surface recipe, per the
#: ``azure.ai.evaluations`` extension. The basename is a constant upstream;
#: the ``.yml`` spelling is tolerated for convenience.
CURRENT_EVAL_DIRNAME = "evals"
CURRENT_EVAL_FILENAMES = ("azure.eval.yaml", "azure.eval.yml")


class EvalSurface(str, Enum):
    """Which azd evaluation command family and extension a recipe requires.

    ``legacy``  -> ``azure.ai.agents``      -> ``azd ai agent eval ...``
    ``current`` -> ``azure.ai.evaluations`` -> ``azd ai eval ...``
    """

    LEGACY = "legacy"
    CURRENT = "current"



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
    dataset_file: Optional[str] = None
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


def _legacy_candidates(root: Path) -> list[Path]:
    """Return legacy recipe candidates, deterministically ordered."""

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

    return sorted({candidate.resolve() for candidate in candidates})


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def find_eval_yaml(workspace: Path, explicit_path: Optional[Path] = None) -> Optional[Path]:
    """Find an azd ``eval.yaml`` recipe under ``workspace``.

    Discovery is deterministic. A single recipe can live at the workspace root
    or under ``src/<agent>/``. Multiple candidates require an explicit
    ``eval_recipe`` path.

    This function covers the legacy surface only. Cross-surface resolution
    lives in :func:`resolve_recipe`.
    """

    root = workspace.resolve()
    if explicit_path is not None:
        path = explicit_path if explicit_path.is_absolute() else root / explicit_path
        return path.resolve()

    unique = _legacy_candidates(root)
    if not unique:
        return None
    if len(unique) > 1:
        display = ", ".join(_display_path(path, root) for path in unique)
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


# ---------------------------------------------------------------------------
# Current surface (``azure.ai.evaluations`` / ``azd ai eval``)
# ---------------------------------------------------------------------------


class CurrentDatasetDecl(BaseModel):
    """A ``datasets[]`` entry in ``evals/azure.eval.yaml``.

    The local file key is ``file``. ``source`` is *not* valid here; that
    spelling belongs to ``evals[].source``.
    """

    name: str
    file: Optional[str] = None
    version: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class CurrentEvaluatorDecl(BaseModel):
    """An ``evaluators[]`` entry: a locally declared evaluator, usually a rubric."""

    name: str
    source: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None
    version: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class CurrentEvaluatorRef(BaseModel):
    """An ``evals[].evaluators[]`` entry."""

    evaluator: str
    name: Optional[str] = None
    version: Optional[str] = None
    initialization_parameters: Dict[str, Any] = Field(default_factory=dict)
    data_mapping: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"evaluator": data}
        return data

    @field_validator("evaluator")
    @classmethod
    def _evaluator_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("azd eval evaluator reference must be non-empty")
        return value

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    @property
    def metric_name(self) -> str:
        """Return the metric key this reference produces."""

        if self.name and self.name.strip():
            return self.name.strip()
        return self.evaluator


class CurrentSourceDecl(BaseModel):
    """An ``evals[].source`` block: trace-sourced or response-sourced input."""

    type: str
    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    lookback_hours: Optional[int] = None
    max_traces: Optional[int] = None
    max_turns: Optional[int] = None
    response_ids: list[str] = Field(default_factory=list)
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("agent_version", mode="before")
    @classmethod
    def _version_to_string(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)

    def describe(self) -> str:
        """Return a short human-readable provenance description."""

        parts = [f"{self.type}"]
        if self.agent_name:
            parts.append(f"agent={self.agent_name}")
        if self.agent_version:
            parts.append(f"version={self.agent_version}")
        if self.lookback_hours is not None:
            parts.append(f"lookback_hours={self.lookback_hours}")
        if self.max_traces is not None:
            parts.append(f"max_traces={self.max_traces}")
        return f"azd:{' '.join(parts)}"


class CurrentTarget(BaseModel):
    """An ``evals[].target`` block."""

    type: str
    name: str

    model_config = ConfigDict(extra="allow")


class CurrentEval(BaseModel):
    """An ``evals[]`` entry."""

    name: str
    id: Optional[str] = None
    description: Optional[str] = None
    dataset: Optional[str] = None
    source: Optional[CurrentSourceDecl] = None
    evaluation_level: Optional[str] = None
    max_samples: Optional[int] = None
    evaluators: list[CurrentEvaluatorRef] = Field(default_factory=list)
    target: Optional[CurrentTarget] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("azd eval name must be non-empty")
        return value


class CurrentEvalRecipe(BaseModel):
    """Tolerant model for ``evals/azure.eval.yaml``.

    Unknown fields are preserved so that additive upstream changes to the
    preview schema do not turn into AgentOps outages.
    """

    datasets: list[CurrentDatasetDecl] = Field(default_factory=list)
    evaluators: list[CurrentEvaluatorDecl] = Field(default_factory=list)
    evals: list[CurrentEval] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    def primary_eval(self) -> CurrentEval:
        """Return the single declared evaluation.

        AgentOps runs exactly one evaluation per invocation, so a recipe
        declaring several requires the user to say which one.
        """

        if not self.evals:
            raise AzdEvalRecipeError(
                "azd eval recipe declares no evaluations under 'evals:'. "
                "Add one evaluation, or point 'eval_recipe:' at a recipe that has one."
            )
        if len(self.evals) > 1:
            names = ", ".join(item.name for item in self.evals)
            raise AzdEvalRecipeAmbiguous(
                "azd eval recipe declares multiple evaluations and AgentOps runs one "
                f"per invocation: {names}. Split them into separate recipes and select "
                "one with 'eval_recipe:' in agentops.yaml."
            )
        return self.evals[0]


def classify_recipe_document(
    data: Any,
    *,
    path: Optional[Path] = None,
    hint: Optional[EvalSurface] = None,
) -> EvalSurface:
    """Classify a parsed recipe document by content.

    Content always wins. ``hint`` (derived from the discovery location) is used
    only when the document is structurally inconclusive, so a minimal legacy
    recipe found at a legacy location still classifies deterministically.
    """

    location = f" at {path}" if path is not None else ""
    if not isinstance(data, dict):
        raise AzdEvalRecipeError(
            f"azd eval recipe{location} is not a YAML mapping, so AgentOps cannot "
            "determine which azd evaluation surface it belongs to."
        )
    if isinstance(data.get("evals"), list):
        return EvalSurface.CURRENT
    if isinstance(data.get("agent"), dict) or "dataset_reference" in data:
        return EvalSurface.LEGACY
    if hint is not None:
        return hint
    raise AzdEvalRecipeError(
        f"azd eval recipe{location} matches neither supported schema. "
        "A current-surface recipe has a sequence-valued 'evals:' key; a legacy "
        "recipe has a mapping-valued 'agent:' key or a 'dataset_reference:' key."
    )


def _surface_hint_for(path: Path) -> Optional[EvalSurface]:
    if path.name in CURRENT_EVAL_FILENAMES:
        return EvalSurface.CURRENT
    if path.name in EVAL_RECIPE_FILENAMES:
        return EvalSurface.LEGACY
    return None


def classify_recipe_file(path: Path) -> EvalSurface:
    """Load ``path`` and classify which azd evaluation surface it targets."""

    try:
        data = load_yaml(path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise AzdEvalRecipeError(f"could not read azd eval recipe {path}: {exc}") from exc
    return classify_recipe_document(data, path=path, hint=_surface_hint_for(path))


def load_current_eval_recipe(path: Path) -> CurrentEvalRecipe:
    """Load and validate a current-surface ``azure.eval.yaml`` recipe."""

    try:
        data = load_yaml(path)
        return CurrentEvalRecipe.model_validate(data)
    except ValidationError as exc:
        raise AzdEvalRecipeError(f"invalid azd eval recipe {path}: {exc}") from exc


def _resolve_relative(reference: str, base_dir: Path) -> Path:
    candidate = Path(reference)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate


def _load_dimensions_document(path: Path) -> list[EvalRubricDimension]:
    if not path.exists():
        raise AzdEvalRecipeError(
            f"azd eval recipe references a rubric definition that does not exist: {path}. "
            "Commit the rubric file or remove the reference."
        )
    try:
        data = load_yaml(path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise AzdEvalRecipeError(f"could not read rubric definition {path}: {exc}") from exc
    return _dimensions_from_mapping(data)


def _dimensions_from_mapping(data: Any) -> list[EvalRubricDimension]:
    if not isinstance(data, dict):
        return []
    raw = data.get("dimensions")
    if not isinstance(raw, list):
        return []
    dimensions: list[EvalRubricDimension] = []
    for entry in raw:
        if isinstance(entry, dict):
            try:
                dimensions.append(EvalRubricDimension.model_validate(entry))
            except ValidationError:
                continue
    return dimensions


def current_evaluator_dimensions(
    declaration: CurrentEvaluatorDecl,
    recipe_dir: Path,
) -> list[EvalRubricDimension]:
    """Return the rubric dimensions a locally declared evaluator produces."""

    definition = declaration.definition
    if isinstance(definition, dict):
        ref = definition.get("$ref")
        if isinstance(ref, str) and ref.strip():
            return _load_dimensions_document(_resolve_relative(ref.strip(), recipe_dir))
        dimensions = _dimensions_from_mapping(definition)
        if dimensions:
            return dimensions
    if declaration.source and declaration.source.strip():
        return _load_dimensions_document(_resolve_relative(declaration.source.strip(), recipe_dir))
    return []


def current_recipe_metric_names(recipe: CurrentEvalRecipe, recipe_path: Path) -> set[str]:
    """Return every metric name a current-surface recipe can produce.

    This is the set that pre-flight threshold binding validates against, so it
    must cover builtin evaluator references, evaluator labels, and the rubric
    dimensions of locally declared evaluators.
    """

    recipe_dir = recipe_path.parent
    declared = {
        declaration.name: declaration
        for declaration in recipe.evaluators
        if declaration.name
    }

    names: set[str] = set()
    for evaluation in recipe.evals:
        for reference in evaluation.evaluators:
            metric = reference.metric_name
            if metric:
                names.add(metric)
            declaration = declared.get(reference.evaluator)
            if declaration is None:
                continue
            for dimension in current_evaluator_dimensions(declaration, recipe_dir):
                dimension_name = dimension.metric_name
                if dimension_name:
                    names.add(dimension_name)
    return names


def current_recipe_dataset_path(
    recipe: CurrentEvalRecipe,
    evaluation: CurrentEval,
    recipe_path: Path,
) -> str:
    """Describe the data an evaluation reads, for result provenance."""

    if evaluation.dataset:
        for declaration in recipe.datasets:
            if declaration.name != evaluation.dataset:
                continue
            if declaration.file:
                return str(_resolve_relative(declaration.file, recipe_path.parent))
            return declaration.name
        return evaluation.dataset
    if evaluation.source is not None:
        return evaluation.source.describe()
    return ""


# ---------------------------------------------------------------------------
# Cross-surface discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecipeResolution:
    """The recipe AgentOps selected, and what it passed over to get there."""

    path: Path
    surface: EvalSurface
    skipped: tuple[Path, ...] = ()
    explicit: bool = False

    @property
    def extension(self) -> str:
        """Return the azd extension id this recipe's surface requires."""

        if self.surface is EvalSurface.CURRENT:
            return "azure.ai.evaluations"
        return "azure.ai.agents"


def _current_candidates(root: Path) -> list[Path]:
    eval_dir = root / CURRENT_EVAL_DIRNAME
    if not eval_dir.is_dir():
        return []
    candidates = [
        eval_dir / filename
        for filename in CURRENT_EVAL_FILENAMES
        if (eval_dir / filename).exists()
    ]
    return sorted({candidate.resolve() for candidate in candidates})


def resolve_recipe(workspace: Path, explicit_path: Optional[Path] = None) -> RecipeResolution:
    """Resolve which azd evaluation recipe a run should use.

    Precedence, per the feature contract:

    1. An explicit ``eval_recipe`` wins outright.
    2. Candidates from both surfaces select the current surface, recording the
       skipped legacy paths so the choice is reported rather than silent.
    3. More than one candidate within a single surface is ambiguous.
    4. No candidate is a configuration error naming both supported locations.
    """

    root = workspace.resolve()

    if explicit_path is not None:
        path = _resolve_relative(str(explicit_path), root).resolve()
        if not path.exists():
            raise AzdEvalRecipeError(
                f"azd eval recipe not found at {path}. Update 'eval_recipe:' in "
                "agentops.yaml, or remove it to use auto-discovery."
            )
        return RecipeResolution(
            path=path,
            surface=classify_recipe_file(path),
            skipped=(),
            explicit=True,
        )

    current = _current_candidates(root)
    legacy = _legacy_candidates(root)

    if len(current) > 1:
        display = ", ".join(_display_path(path, root) for path in current)
        raise AzdEvalRecipeAmbiguous(
            "multiple azd eval recipes found; set 'eval_recipe' in agentops.yaml "
            f"to choose one: {display}"
        )

    if current:
        return RecipeResolution(
            path=current[0],
            surface=classify_recipe_file(current[0]),
            skipped=tuple(legacy),
            explicit=False,
        )

    if len(legacy) > 1:
        display = ", ".join(_display_path(path, root) for path in legacy)
        raise AzdEvalRecipeAmbiguous(
            "multiple azd eval recipes found; set 'eval_recipe' in agentops.yaml "
            f"to choose one: {display}"
        )

    if legacy:
        return RecipeResolution(
            path=legacy[0],
            surface=classify_recipe_file(legacy[0]),
            skipped=(),
            explicit=False,
        )

    raise AzdEvalRecipeError(
        "azd eval recipe not found. AgentOps looks for "
        f"'{CURRENT_EVAL_DIRNAME}/{CURRENT_EVAL_FILENAMES[0]}' (azd ai eval) and "
        "'eval.yaml' at the workspace root or under 'src/<agent>/' "
        "(azd ai agent eval). Generate one with `agentops eval init`, or set "
        "'eval_recipe:' in agentops.yaml. If you want the AgentOps local engine "
        "instead, set 'execution: local'."
    )

