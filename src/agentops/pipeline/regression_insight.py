"""Deterministic diffing between two evaluation runs.

Used both to explain a detected regression (see ``build_regression_insight``)
and to power Cockpit's version-history view, which lists what changed
between consecutive runs independent of whether a regression occurred.

Comparisons are made purely from fields already recorded on each run's
``RunResult`` (target, config, dataset, evaluators, thresholds) - no git
tree access, no network calls, no LLM calls, per the feature's determinism
requirement.

Caveat: a Foundry prompt-agent system-prompt change is only detected via a
``name:version`` bump (see ``_target_changes``) - editing a prompt's content
without publishing a new version produces no ``system_prompt`` entry in
``changed_inputs``, since no prompt text or content hash is captured on
``RunResult`` to diff against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentops.core.results import (
    ChangedInput,
    RegressedMetric,
    RegressionInsight,
    RunResult,
    TargetInfo,
)
from agentops.pipeline.commit_info import commit_exists_locally

_TRACKED_CONFIG_FIELDS = ("dataset", "evaluators", "thresholds")

_SUGGESTION_LABELS: Dict[str, str] = {
    "system_prompt": "prompt change",
    "model": "model change",
    "dataset": "dataset change",
    "evaluators": "evaluator-set change",
    "thresholds": "threshold change",
}

# Fallback for metrics with no threshold criteria recorded on either
# compared run - every evaluator's default threshold (see
# `core/evaluators.py`) is `>=` except this one (`<=`). Only a fallback:
# `agentops.yaml` lets *any* metric name declare a `<`/`<=` threshold
# (and `execution: azd` can introduce entirely custom metric names this
# way - see AGENTS.md), so a metric's own recorded `ThresholdEvaluation`
# criteria (see `_threshold_criteria`) is the real source of truth
# whenever it's available; this set only covers the common case where
# neither compared run happens to carry one. Lives here (rather than
# `pipeline.comparison`, its only other consumer) because this module has
# no dependency on `comparison`, while `comparison` already depends on
# this module for `build_regression_insight` - putting it here avoids a
# circular import.
LOWER_IS_BETTER_METRICS = frozenset({"avg_latency_seconds"})


def _threshold_criteria(run: RunResult, metric: str) -> Optional[str]:
    """The ``<=``/``>=``/etc. operator configured for ``metric`` on ``run``,
    from its own recorded ``ThresholdEvaluation`` list, if any."""
    for threshold in run.thresholds:
        if threshold.metric == metric:
            return threshold.criteria
    return None


def metric_threshold_criteria(
    metric: str, *runs: Optional[RunResult]
) -> Optional[str]:
    """The first threshold criteria found for ``metric`` across ``runs``,
    checked in order - callers typically pass the current/latest run
    first, then the baseline/previous one, so either side recording a
    threshold for this metric is enough to know its direction."""
    for run in runs:
        if run is None:
            continue
        criteria = _threshold_criteria(run, metric)
        if criteria is not None:
            return criteria
    return None


def _is_lower_is_better(metric: str, *, criteria: Optional[str] = None) -> bool:
    """Whether a lower value is the better outcome for ``metric``.

    ``criteria`` (a recorded threshold operator, from
    ``metric_threshold_criteria``) wins when given - it reflects what this
    specific metric was actually configured to mean, unlike
    ``LOWER_IS_BETTER_METRICS``, which is only a fallback guess for the one
    built-in metric known to commonly be lower-is-better.

    A boolean threshold (``"false"``/``"true"``, for a metric whose values
    are 0/1) is treated the same as ``<=``/``>=``: passing means 0
    (``"false"``), so lower is better, same as it would be for a
    continuous metric thresholded that way. An equality threshold
    (``"=="``) has no well-defined "lower is better" at all - what's
    "closer to passing" depends on the recorded target value, not raw
    magnitude - so it's deliberately left unhandled here and falls through
    to the same default as no criteria at all, rather than guessing.
    """
    if criteria is not None:
        if criteria == "false":
            return True
        if criteria == "true":
            return False
        if criteria.startswith("<"):
            return True
        if criteria.startswith(">"):
            return False
    return metric in LOWER_IS_BETTER_METRICS


def metric_improved(
    metric: str,
    current: Optional[float],
    baseline: Optional[float],
    *,
    criteria: Optional[str] = None,
) -> Optional[bool]:
    """Whether ``current`` is a better outcome than ``baseline`` for ``metric``.

    Returns ``None`` when the values are missing or equal (no judgement to
    make), ``True`` when ``current`` is the better value, ``False`` when
    it's worse - accounting for ``metric``'s direction (see
    ``_is_lower_is_better``).
    """
    if current is None or baseline is None or current == baseline:
        return None
    if _is_lower_is_better(metric, criteria=criteria):
        return current < baseline
    return current > baseline


def regression_severity(
    metric: str,
    from_value: float,
    to_value: float,
    *,
    criteria: Optional[str] = None,
) -> float:
    """How severely ``metric`` regressed from ``from_value`` to ``to_value``,
    as a fraction of ``from_value`` when that's meaningful, else as an
    absolute change. Used only to order ``RegressionInsight.regressed_metrics``
    worst-first - direction-aware (see ``_is_lower_is_better``), so a
    lower-is-better metric (e.g. latency, or any custom metric with a
    ``<=``/``<`` threshold) getting worse produces a positive value just
    like any other regression. A ``from_value`` at or below zero can't
    produce a meaningful ratio, so the absolute change is used instead of
    collapsing to zero.
    """
    worse_by = (
        to_value - from_value
        if _is_lower_is_better(metric, criteria=criteria)
        else from_value - to_value
    )
    if worse_by <= 0:
        return 0.0
    if from_value <= 0:
        return worse_by
    return worse_by / from_value


def resolve_report_url(run: RunResult, *, results_path: Optional[Path] = None) -> Optional[str]:
    """The Foundry Evaluations deep-link for ``run``, when it was published.

    Checks the run's own recorded config first (``execution: cloud`` embeds
    ``report_url`` there before ``results.json`` is even written), then -
    when ``results_path`` is given - a sibling ``cloud_evaluation.json``
    (written by a completed local ``publish: true`` Classic Foundry publish
    step, which happens *after* ``results.json`` and so is never in the
    run's own config). Returns ``None`` - never raises - whenever neither
    source has a URL, e.g. the run was never published.
    """
    cloud_evaluation = (run.config or {}).get("cloud_evaluation")
    if isinstance(cloud_evaluation, dict):
        url = cloud_evaluation.get("report_url")
        if isinstance(url, str) and url:
            return url

    if results_path is not None:
        sidecar = results_path.parent / "cloud_evaluation.json"
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict):
            url = data.get("report_url")
            if isinstance(url, str) and url:
                return url

    return None


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


def _join_with_and(items: List[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _describe_metric_change(metric: RegressedMetric) -> str:
    # Every entry here is, by construction, a regression - and for both a
    # higher-is-better metric dropping and a lower-is-better metric (e.g.
    # latency) rising, "got worse" always matches this raw numeric
    # comparison, so no direction lookup is needed just to word it.
    verb = "dropped" if metric.to_value < metric.from_value else "increased"
    return f"{metric.metric} {verb} from {metric.from_value:.2f} to {metric.to_value:.2f}"


def _explanation(
    *,
    regressed_metrics: List[RegressedMetric],
    changed_inputs: List[ChangedInput],
    from_short_sha: str,
    to_short_sha: str,
) -> str:
    metrics_text = _join_with_and([_describe_metric_change(m) for m in regressed_metrics])
    base = f"Run {from_short_sha} → {to_short_sha}: {metrics_text}."
    if not changed_inputs:
        return base

    cause = _join_with_and([c.description for c in changed_inputs])
    return f"{base} Likely cause: {cause}."


def _suggested_action(changed_inputs: List[ChangedInput]) -> Optional[str]:
    if not changed_inputs:
        return None
    labels = [_SUGGESTION_LABELS.get(c.field, f"{c.field} change") for c in changed_inputs]
    if len(labels) == 1:
        return f"Review the {labels[0]} to confirm it's the cause, and revert it if so."
    return f"Review the {_join_with_and(labels)}; consider reverting one at a time to isolate the cause."


# Matches the `/versions/<version>` segment a Foundry hosted-agent URL
# carries (see `core.agentops_config._HOSTED_AGENT_REFERENCE_RE` - the
# same pattern, kept independent since `core/` must stay free of
# `pipeline`/`agent` imports). A hosted agent's `url` is otherwise the
# strongest identity signal available (project + agent name, already
# qualified) - but it's version-specific, so used as-is it would make
# every version bump start a new lineage, exactly the change this feature
# exists to compare across.
_HOSTED_AGENT_VERSION_RE = re.compile(r"/versions/[^/?#]+", re.IGNORECASE)


def normalize_agent_url(url: str) -> str:
    """Strip the ``/versions/<version>`` segment from a Foundry hosted
    agent URL, keeping the project+agent-qualified rest of it intact."""
    return _HOSTED_AGENT_VERSION_RE.sub("", url)


def agent_identity_from_fields(
    *,
    name: Optional[str],
    url: Optional[str],
    kind: Optional[str],
    raw: Optional[str],
) -> Optional[str]:
    """Shared agent-identity resolution for lineage grouping - the single
    source of truth used by ``cockpit._version_lineage_key``,
    ``results_history._lineage_key``, and this module's
    ``_agent_identity`` (``RunResult``-based), so the three can't drift
    apart again.

    ``url`` wins over ``name`` when both are present: for
    ``foundry_hosted``, ``name`` is only the agent-name fragment parsed
    *out of* that same URL (see ``TargetInfo``'s docstring), so two
    different Foundry projects with a same-named agent would otherwise
    collide on an identical, project-blind ``name``. The URL itself is
    normalized (``normalize_agent_url``) to strip its version segment, so
    a version bump doesn't start a new lineage - the opposite of using
    ``name`` alone, which is already version-blind.

    ``raw`` is the fallback for every remaining kind except
    ``model_direct`` (``model:<deployment>``), where ``raw`` itself embeds
    the deployment (e.g. ``"model:gpt-4o"`` vs ``"model:gpt-4o-mini"``) -
    using it there would make a deployment change look like a different
    agent entirely. ``kind`` has no such problem: it's the constant
    ``"model_direct"`` for every run of this kind.

    Known gap, not fixed here: ``foundry_prompt`` has neither ``url`` nor
    any other project-qualified field - just ``name``/``version`` - so two
    different projects with a same-named prompt agent still collide.
    Fixing that needs a new field capturing the project endpoint on
    ``TargetInfo`` for prompt agents, which is a bigger, separate change.
    """
    if url:
        return normalize_agent_url(url)
    if name:
        return name
    if kind == "model_direct":
        return kind
    return raw


def _agent_identity(target: TargetInfo) -> Optional[str]:
    """``agent_identity_from_fields`` for a ``RunResult.target`` directly."""
    return agent_identity_from_fields(
        name=target.name, url=target.url, kind=target.kind, raw=target.raw
    )


def same_lineage(from_run: RunResult, to_run: RunResult) -> bool:
    """Whether two runs are comparable for causal regression attribution
    (FR-005): same agent identity - but deliberately version/deployment-
    blind, matching the lineage rule Doctor's own ``previous_run``
    selection already enforces by construction (see
    ``agent.checks.regression``). An explicit ``--baseline`` file has no
    such guarantee built in (the user can point it at any
    ``results.json``), so ``build_regression_insight`` checks this itself
    rather than trusting every caller to have already filtered for it -
    comparing two runs of entirely different agents could otherwise
    produce a "likely cause" that isn't real.

    Deliberately does *not* require the same ``dataset_path`` or
    ``evaluators``: those are exactly the kind of change this feature
    must still be able to attribute a regression to (FR-006, and
    ``build_changed_inputs``'s own dataset/evaluator-change detection) -
    blocking on them would make that detection unreachable. ``dataset_path``
    is also not checkout-stable (local runs persist an absolute, resolved
    path - see ``services.dataset_source``), so two runs of the very same
    repo-relative dataset produced from different checkouts (e.g. a
    committed baseline vs. a fresh CI run) would otherwise never match.
    """
    return _agent_identity(from_run.target) == _agent_identity(to_run.target)


def build_regression_insight(
    from_run: RunResult,
    to_run: RunResult,
    *,
    metrics: List[str],
    workspace: Optional[Path] = None,
    from_report_url: Optional[str] = None,
    to_report_url: Optional[str] = None,
) -> Optional[RegressionInsight]:
    """Explain a regression on ``metrics`` between two comparable runs.

    ``metrics`` is every metric known to have regressed between the two
    runs (not just the worst one) - all of them are listed in the returned
    insight's ``regressed_metrics``, ordered worst-first
    (``regression_severity``), so the explanation and the report/Cockpit
    surfaces consuming it don't silently drop all but one when several
    metrics move together.

    Returns ``None`` (produces no fabricated cause) when either run lacks
    commit metadata, when the two runs aren't comparable in the first
    place (``same_lineage`` - FR-005: different agent, dataset, or
    evaluator set), or when none of ``metrics`` has a value on both runs,
    per FR-009 - an individual metric missing a value on either side is
    just skipped rather than failing the whole insight. Otherwise diffs the
    runs' recorded fields (see ``build_changed_inputs``) and renders a
    plain-language explanation plus a short suggested corrective action.

    ``workspace`` is the directory the two commits' repository lives in;
    it's forwarded to the local git lookups below so they run against the
    right checkout instead of the current process's cwd. ``from_report_url``
    /``to_report_url`` (see ``resolve_report_url``) are carried through
    as-is - this function does no file I/O to resolve them itself, since
    only the caller knows whether a run's ``results.json`` path is
    available to check for a sidecar ``cloud_evaluation.json``.
    """
    if from_run.commit is None or to_run.commit is None:
        return None
    if not same_lineage(from_run, to_run):
        return None

    regressed_metrics: List[RegressedMetric] = []
    for metric in metrics:
        from_value = from_run.aggregate_metrics.get(metric)
        to_value = to_run.aggregate_metrics.get(metric)
        if from_value is None or to_value is None:
            continue
        regressed_metrics.append(
            RegressedMetric(metric=metric, from_value=from_value, to_value=to_value)
        )
    if not regressed_metrics:
        return None
    regressed_metrics.sort(
        key=lambda m: regression_severity(
            m.metric,
            m.from_value,
            m.to_value,
            criteria=metric_threshold_criteria(m.metric, to_run, from_run),
        ),
        reverse=True,
    )

    changed_inputs = build_changed_inputs(from_run, to_run)
    commits_available_locally = commit_exists_locally(
        from_run.commit.sha, workspace=workspace
    ) and commit_exists_locally(to_run.commit.sha, workspace=workspace)

    return RegressionInsight(
        from_run_id=from_run.started_at,
        to_run_id=to_run.started_at,
        from_commit=from_run.commit,
        to_commit=to_run.commit,
        regressed_metrics=regressed_metrics,
        changed_inputs=changed_inputs,
        explanation=_explanation(
            regressed_metrics=regressed_metrics,
            changed_inputs=changed_inputs,
            from_short_sha=from_run.commit.short_sha,
            to_short_sha=to_run.commit.short_sha,
        ),
        suggested_action=_suggested_action(changed_inputs),
        commits_available_locally=commits_available_locally,
        from_report_url=from_report_url,
        to_report_url=to_report_url,
    )
