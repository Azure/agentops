"""Evaluator catalog and auto-selection for AgentOps 1.0.

This module replaces the layered ``bundle.yaml`` system. There is no
user-facing ``scenario`` concept. Evaluators are picked from two inputs:

1. The resolved target kind (agent vs model). Model targets only get the
   baseline quality evaluators — agent-specific evaluators are skipped even
   if the dataset contains those fields.
2. The shape of the dataset rows:

   * Always: baseline quality evaluators (Coherence, Fluency, Similarity,
     F1Score).
   * If rows include ``context``: add RAG evaluators (Groundedness,
     Retrieval, Relevance, ResponseCompleteness).
   * If rows include ``tool_calls`` or ``tool_definitions``: add agent
     evaluators (ToolCallAccuracy, IntentResolution, TaskAdherence).

The :func:`select_evaluators` function returns a list of resolved
:class:`EvaluatorPreset` objects. Each preset carries its class name, the
input mapping it requires, the score key it produces, and a default
threshold. The runner uses these presets to instantiate
``azure-ai-evaluation`` evaluator classes against each dataset row.

Power users can override the auto-selection by listing evaluator names in
``agentops.yaml`` under ``evaluators:``. When set, the override list is the
final word — no auto-detection runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from agentops.core.agentops_config import TargetKind, TargetResolution, Threshold


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluatorPreset:
    """Metadata for a single evaluator known to AgentOps.

    ``input_mapping`` keys are the parameter names the evaluator class
    expects; values use the placeholder syntax ``$prompt``, ``$prediction``,
    ``$context``, ``$expected``, ``$tool_calls``, ``$tool_definitions``
    which the runner resolves per row.
    """

    name: str
    class_name: str
    score_key: str
    input_mapping: Dict[str, str]
    default_threshold: Optional[Threshold] = None
    #: Categories that this evaluator belongs to. Used by the inference rules.
    categories: FrozenSet[str] = field(default_factory=frozenset)
    #: Set when this evaluator is not safe to run for raw model deployments.
    agent_only: bool = False
    #: When True and the row carries ``tool_calls``, the runner upgrades the
    #: ``query`` and ``response`` kwargs from plain strings to conversation
    #: message lists that include the agent's tool_call + tool_result trace.
    #: This is required for evaluators that judge agent reasoning (e.g.
    #: TaskAdherence, IntentResolution) — without the trace they only see a
    #: short final answer and consistently score it as 1/5.
    needs_conversation: bool = False


def _t(metric: str, criteria: str, value: float) -> Threshold:
    return Threshold(metric=metric, criteria=criteria, value=value)  # type: ignore[arg-type]


_QUALITY_BASELINE: Tuple[EvaluatorPreset, ...] = (
    EvaluatorPreset(
        name="CoherenceEvaluator",
        class_name="CoherenceEvaluator",
        score_key="coherence",
        input_mapping={"query": "$prompt", "response": "$prediction"},
        default_threshold=_t("coherence", ">=", 3.0),
        categories=frozenset({"quality"}),
    ),
    EvaluatorPreset(
        name="FluencyEvaluator",
        class_name="FluencyEvaluator",
        score_key="fluency",
        input_mapping={"response": "$prediction"},
        default_threshold=_t("fluency", ">=", 3.0),
        categories=frozenset({"quality"}),
    ),
    EvaluatorPreset(
        name="SimilarityEvaluator",
        class_name="SimilarityEvaluator",
        score_key="similarity",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "ground_truth": "$expected",
        },
        default_threshold=_t("similarity", ">=", 3.0),
        categories=frozenset({"quality"}),
    ),
    EvaluatorPreset(
        name="F1ScoreEvaluator",
        class_name="F1ScoreEvaluator",
        score_key="f1_score",
        input_mapping={
            "response": "$prediction",
            "ground_truth": "$expected",
        },
        default_threshold=_t("f1_score", ">=", 0.5),
        categories=frozenset({"quality"}),
    ),
)


_RAG_EVALUATORS: Tuple[EvaluatorPreset, ...] = (
    EvaluatorPreset(
        name="GroundednessEvaluator",
        class_name="GroundednessEvaluator",
        score_key="groundedness",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "context": "$context",
        },
        default_threshold=_t("groundedness", ">=", 3.0),
        categories=frozenset({"rag"}),
        agent_only=True,
    ),
    EvaluatorPreset(
        name="RelevanceEvaluator",
        class_name="RelevanceEvaluator",
        score_key="relevance",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "context": "$context",
        },
        default_threshold=_t("relevance", ">=", 3.0),
        categories=frozenset({"rag"}),
        agent_only=True,
    ),
    EvaluatorPreset(
        name="RetrievalEvaluator",
        class_name="RetrievalEvaluator",
        score_key="retrieval",
        input_mapping={"query": "$prompt", "context": "$context"},
        default_threshold=_t("retrieval", ">=", 3.0),
        categories=frozenset({"rag"}),
        agent_only=True,
    ),
    EvaluatorPreset(
        name="ResponseCompletenessEvaluator",
        class_name="ResponseCompletenessEvaluator",
        score_key="response_completeness",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "ground_truth": "$expected",
        },
        default_threshold=_t("response_completeness", ">=", 3.0),
        categories=frozenset({"rag"}),
        agent_only=True,
    ),
)


_TOOL_USE_EVALUATORS: Tuple[EvaluatorPreset, ...] = (
    EvaluatorPreset(
        name="ToolCallAccuracyEvaluator",
        class_name="ToolCallAccuracyEvaluator",
        score_key="tool_call_accuracy",
        input_mapping={
            "query": "$prompt",
            "tool_calls": "$tool_calls",
            "tool_definitions": "$tool_definitions",
        },
        default_threshold=_t("tool_call_accuracy", ">=", 0.7),
        categories=frozenset({"agent"}),
        agent_only=True,
    ),
    EvaluatorPreset(
        name="IntentResolutionEvaluator",
        class_name="IntentResolutionEvaluator",
        score_key="intent_resolution",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "tool_definitions": "$tool_definitions",
        },
        default_threshold=_t("intent_resolution", ">=", 3.0),
        categories=frozenset({"agent"}),
        agent_only=True,
        needs_conversation=True,
    ),
    EvaluatorPreset(
        name="TaskAdherenceEvaluator",
        class_name="TaskAdherenceEvaluator",
        score_key="task_adherence",
        input_mapping={
            "query": "$prompt",
            "response": "$prediction",
            "tool_definitions": "$tool_definitions",
        },
        # azure-ai-evaluation's TaskAdherenceEvaluator returns a binary
        # 0/1 score (0 = flagged, 1 = adheres) — *not* a 1–5 Likert scale
        # like IntentResolutionEvaluator. We default to >=0.5 so a score
        # of 1.0 passes and 0.0 fails.
        default_threshold=_t("task_adherence", ">=", 0.5),
        categories=frozenset({"agent"}),
        agent_only=True,
        needs_conversation=True,
    ),
)


_LATENCY = EvaluatorPreset(
    name="avg_latency_seconds",
    class_name="_latency",
    score_key="avg_latency_seconds",
    input_mapping={},
    default_threshold=_t("avg_latency_seconds", "<=", 10.0),
    categories=frozenset({"runtime"}),
)


CATALOG: Dict[str, EvaluatorPreset] = {
    preset.name: preset
    for preset in (
        *_QUALITY_BASELINE,
        *_RAG_EVALUATORS,
        *_TOOL_USE_EVALUATORS,
        _LATENCY,
    )
}


# ---------------------------------------------------------------------------
# Dataset shape detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetShape:
    """Boolean flags summarising the columns present in a dataset."""

    has_context: bool
    has_tool_calls: bool
    has_tool_definitions: bool
    row_count: int

    @property
    def looks_rag(self) -> bool:
        return self.has_context

    @property
    def looks_tool_use(self) -> bool:
        return self.has_tool_calls or self.has_tool_definitions


def detect_dataset_shape(dataset_path: Path, *, sample: int = 50) -> DatasetShape:
    """Inspect up to ``sample`` rows of ``dataset_path`` and report the shape.

    Truthy values are required — empty strings, empty lists, and ``None`` do
    not count as the field being present.
    """
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset file not found: {dataset_path}")

    has_context = False
    has_tool_calls = False
    has_tool_definitions = False
    count = 0

    with dataset_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            count += 1
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{dataset_path}: invalid JSON on line {count}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"{dataset_path}: line {count} is not a JSON object"
                )

            if not has_context and row.get("context"):
                has_context = True
            if not has_tool_calls and row.get("tool_calls"):
                has_tool_calls = True
            if not has_tool_definitions and row.get("tool_definitions"):
                has_tool_definitions = True

            if count >= sample and (
                has_context and (has_tool_calls or has_tool_definitions)
            ):
                # Already saw both signals; no need to keep reading.
                break

    if count == 0:
        raise ValueError(f"{dataset_path}: dataset is empty")

    return DatasetShape(
        has_context=has_context,
        has_tool_calls=has_tool_calls,
        has_tool_definitions=has_tool_definitions,
        row_count=count,
    )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def select_evaluators(
    target: TargetResolution,
    shape: DatasetShape,
    *,
    overrides: Optional[List[str]] = None,
) -> List[EvaluatorPreset]:
    """Return the ordered list of evaluators to run.

    When ``overrides`` is provided it wins outright — the inference rules are
    bypassed. Each name must exist in :data:`CATALOG` or a ``ValueError`` is
    raised.

    Otherwise the rules are:

    * Always include the four baseline quality evaluators.
    * If the target is a raw model, stop here. Agent-specific evaluators are
      not meaningful (no tool calls, no retrieved context).
    * If the dataset has ``context`` rows, add the RAG evaluators.
    * If the dataset has ``tool_calls`` or ``tool_definitions``, add the agent
      evaluators.
    * Always append the runtime ``avg_latency_seconds`` evaluator.
    """
    if overrides:
        resolved: List[EvaluatorPreset] = []
        for name in overrides:
            preset = CATALOG.get(name)
            if preset is None:
                known = ", ".join(sorted(CATALOG.keys()))
                raise ValueError(
                    f"unknown evaluator override {name!r}. "
                    f"Known evaluators: {known}"
                )
            resolved.append(preset)
        return resolved

    selected: List[EvaluatorPreset] = list(_QUALITY_BASELINE)

    if _is_agent_target(target.kind):
        if shape.looks_rag:
            selected.extend(_RAG_EVALUATORS)
        if shape.looks_tool_use:
            selected.extend(_TOOL_USE_EVALUATORS)
            # F1ScoreEvaluator and SimilarityEvaluator compare the
            # assistant's natural-language reply against ``expected``. In
            # tool-using datasets ``expected`` is conventionally a behavior
            # description (e.g. "Calls lookup_order with order_id='ORD-12345'")
            # rather than the literal reply, so token overlap and semantic
            # similarity are meaningless and gate well-behaved agents on a
            # metric that does not apply. Drop both from the selection.
            _drop = {"F1ScoreEvaluator", "SimilarityEvaluator"}
            selected = [p for p in selected if p.name not in _drop]

    selected.append(_LATENCY)
    return selected


def _is_agent_target(kind: TargetKind) -> bool:
    return kind in {"foundry_prompt", "foundry_hosted", "http_json"}


def merge_thresholds(
    presets: List[EvaluatorPreset],
    user_thresholds: List[Threshold],
) -> List[Threshold]:
    """Combine evaluator default thresholds with user overrides.

    User entries override the preset default for the same metric. Metrics
    listed by the user that don't correspond to any selected preset are kept
    as-is — the threshold engine will report them as unmet rather than
    silently drop them.
    """
    by_metric: Dict[str, Threshold] = {}
    for preset in presets:
        if preset.default_threshold is not None:
            by_metric[preset.default_threshold.metric] = preset.default_threshold
    for override in user_thresholds:
        by_metric[override.metric] = override
    # Preserve preset order, then append user-only metrics in original order.
    ordered: List[Threshold] = []
    seen: set[str] = set()
    for preset in presets:
        if preset.default_threshold is not None:
            metric = preset.default_threshold.metric
            ordered.append(by_metric[metric])
            seen.add(metric)
    for override in user_thresholds:
        if override.metric not in seen:
            ordered.append(override)
            seen.add(override.metric)
    return ordered
