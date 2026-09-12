"""Deterministic diffing between two evaluation runs.

Used both to explain a detected regression (see ``build_regression_insight``)
and to power Cockpit's version-history view, which lists what changed
between consecutive runs independent of whether a regression occurred.

Comparisons are made purely from fields already recorded on each run's
``RunResult`` (target, config, dataset, evaluators, thresholds) - no git
tree access, no network calls, no LLM calls, per the feature's determinism
requirement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agentops.core.results import ChangedInput, RegressionInsight, RunResult
from agentops.pipeline.commit_info import commit_exists_locally

_TRACKED_CONFIG_FIELDS = ("dataset", "evaluators", "thresholds")

_SUGGESTION_LABELS: Dict[str, str] = {
    "system_prompt": "prompt change",
    "model": "model change",
    "dataset": "dataset change",
    "evaluators": "evaluator-set change",
    "thresholds": "threshold change",
}


def _target_changes(from_run: RunResult, to_run: RunResult) -> List[ChangedInput]:
    changes: List[ChangedInput] = []
    from_target = from_run.target
    to_target = to_run.target

    if from_target.name != to_target.name or from_target.version != to_target.version:
        from_value = _format_name_version(from_target.name, from_target.version)
        to_value = _format_name_version(to_target.name, to_target.version)
        changes.append(
            ChangedInput(
                field="system_prompt",
                description="the system prompt changed",
                from_value=from_value,
                to_value=to_value,
            )
        )

    if from_target.deployment != to_target.deployment:
        changes.append(
            ChangedInput(
                field="model",
                description=(
                    f"the model changed from {from_target.deployment} to "
                    f"{to_target.deployment}"
                    if from_target.deployment and to_target.deployment
                    else "the model/deployment changed"
                ),
                from_value=from_target.deployment,
                to_value=to_target.deployment,
            )
        )

    return changes


def _format_name_version(name: Any, version: Any) -> Any:
    if name is None and version is None:
        return None
    return f"{name}:{version}"


def _config_changes(from_run: RunResult, to_run: RunResult) -> List[ChangedInput]:
    changes: List[ChangedInput] = []
    from_config: Dict[str, Any] = from_run.config or {}
    to_config: Dict[str, Any] = to_run.config or {}

    from_dataset = from_run.dataset_path
    to_dataset = to_run.dataset_path
    if from_dataset != to_dataset:
        changes.append(
            ChangedInput(
                field="dataset",
                description=f"dataset changed from {from_dataset} to {to_dataset}",
                from_value=str(from_dataset) if from_dataset is not None else None,
                to_value=str(to_dataset) if to_dataset is not None else None,
            )
        )

    from_evaluators = sorted(from_run.evaluators)
    to_evaluators = sorted(to_run.evaluators)
    if from_evaluators != to_evaluators:
        changes.append(
            ChangedInput(
                field="evaluators",
                description=(
                    f"evaluator set changed from {from_evaluators} to {to_evaluators}"
                ),
                from_value=", ".join(from_evaluators) or None,
                to_value=", ".join(to_evaluators) or None,
            )
        )

    from_thresholds = from_config.get("thresholds")
    to_thresholds = to_config.get("thresholds")
    if from_thresholds != to_thresholds:
        changes.append(
            ChangedInput(
                field="thresholds",
                description="threshold configuration changed",
                from_value=str(from_thresholds) if from_thresholds is not None else None,
                to_value=str(to_thresholds) if to_thresholds is not None else None,
            )
        )

    return changes


def build_changed_inputs(from_run: RunResult, to_run: RunResult) -> List[ChangedInput]:
    """Deterministically list every tracked field that differs between two runs.

    Compares the evaluated agent's target (system prompt version, model
    deployment) and tracked run configuration (dataset, evaluators,
    thresholds). Returns an empty list when nothing tracked changed.
    """
    return _target_changes(from_run, to_run) + _config_changes(from_run, to_run)


def _explanation(
    *,
    metric: str,
    from_value: float,
    to_value: float,
    changed_inputs: List[ChangedInput],
    from_short_sha: str,
    to_short_sha: str,
) -> str:
    direction = "dropped" if to_value < from_value else "changed"
    base = (
        f"Run {from_short_sha} → {to_short_sha}: {metric} {direction} "
        f"from {from_value:.2f} to {to_value:.2f}."
    )
    if not changed_inputs:
        return base

    descriptions = [c.description for c in changed_inputs]
    if len(descriptions) == 1:
        cause = descriptions[0]
    elif len(descriptions) == 2:
        cause = f"{descriptions[0]} and {descriptions[1]}"
    else:
        cause = ", ".join(descriptions[:-1]) + f", and {descriptions[-1]}"
    return f"{base} Likely cause: {cause}."


def _suggested_action(changed_inputs: List[ChangedInput]) -> Optional[str]:
    if not changed_inputs:
        return None
    labels = [_SUGGESTION_LABELS.get(c.field, f"{c.field} change") for c in changed_inputs]
    if len(labels) == 1:
        return f"Review the {labels[0]} to confirm it's the cause, and revert it if so."
    joined = ", ".join(labels[:-1]) + f", and {labels[-1]}" if len(labels) > 2 else " and ".join(labels)
    return f"Review the {joined}; consider reverting one at a time to isolate the cause."


def build_regression_insight(
    from_run: RunResult,
    to_run: RunResult,
    *,
    metric: str,
) -> Optional[RegressionInsight]:
    """Explain a regression on ``metric`` between two comparable runs.

    Returns ``None`` (produces no fabricated cause) when either run lacks
    commit metadata or the metric's value isn't present on both runs,
    per FR-009. Otherwise diffs the runs' recorded fields (see
    ``build_changed_inputs``) and renders a plain-language explanation plus
    a short suggested corrective action.
    """
    if from_run.commit is None or to_run.commit is None:
        return None

    from_value = from_run.aggregate_metrics.get(metric)
    to_value = to_run.aggregate_metrics.get(metric)
    if from_value is None or to_value is None:
        return None

    changed_inputs = build_changed_inputs(from_run, to_run)
    used_git_diff = commit_exists_locally(from_run.commit.sha) and commit_exists_locally(
        to_run.commit.sha
    )

    return RegressionInsight(
        from_run_id=from_run.started_at,
        to_run_id=to_run.started_at,
        from_commit=from_run.commit,
        to_commit=to_run.commit,
        metric=metric,
        from_value=from_value,
        to_value=to_value,
        changed_inputs=changed_inputs,
        explanation=_explanation(
            metric=metric,
            from_value=from_value,
            to_value=to_value,
            changed_inputs=changed_inputs,
            from_short_sha=from_run.commit.short_sha,
            to_short_sha=to_run.commit.short_sha,
        ),
        suggested_action=_suggested_action(changed_inputs),
        used_git_diff=used_git_diff,
    )
