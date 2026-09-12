"""Tests for deterministic run-to-run diffing (``pipeline.regression_insight``)."""

from __future__ import annotations

from agentops.core.results import CommitInfo, RunResult, RunSummary, TargetInfo
from agentops.pipeline import regression_insight


def _commit(sha: str = "a" * 40, *, short_sha: str | None = None) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=short_sha or sha[:7],
        subject="A commit",
        author="Dev",
        authored_at="2026-09-01T10:00:00+00:00",
        source="ci",
    )


def _run(
    *,
    name: str = "greeter",
    version: str = "3",
    deployment: str | None = None,
    dataset_path: str = "data/smoke.jsonl",
    evaluators: list[str] | None = None,
    thresholds: dict | None = None,
    accuracy: float = 0.9,
    commit: CommitInfo | None = None,
) -> RunResult:
    return RunResult(
        started_at="2026-09-01T10:00:00+00:00",
        finished_at="2026-09-01T10:00:01+00:00",
        duration_seconds=1.0,
        target=TargetInfo(
            kind="foundry_prompt",
            raw=f"{name}:{version}",
            name=name,
            version=version,
            deployment=deployment,
        ),
        dataset_path=dataset_path,
        evaluators=evaluators or ["CoherenceEvaluator"],
        aggregate_metrics={"accuracy": accuracy},
        summary=RunSummary(
            items_total=1,
            items_passed_all=1,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
        config={"thresholds": thresholds or {}},
        commit=commit,
    )


def test_no_changes_returns_empty_list():
    from_run = _run()
    to_run = _run()

    assert regression_insight.build_changed_inputs(from_run, to_run) == []


def test_prompt_version_change_is_detected():
    from_run = _run(version="3")
    to_run = _run(version="4")

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    assert len(changes) == 1
    assert changes[0].field == "system_prompt"
    assert changes[0].from_value == "greeter:3"
    assert changes[0].to_value == "greeter:4"


def test_model_deployment_change_is_detected():
    from_run = _run(deployment="gpt-4o")
    to_run = _run(deployment="gpt-4o-mini")

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    assert len(changes) == 1
    assert changes[0].field == "model"
    assert "gpt-4o" in changes[0].description
    assert "gpt-4o-mini" in changes[0].description
    assert changes[0].from_value == "gpt-4o"
    assert changes[0].to_value == "gpt-4o-mini"


def test_dataset_change_is_detected():
    from_run = _run(dataset_path="data/a.jsonl")
    to_run = _run(dataset_path="data/b.jsonl")

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    assert len(changes) == 1
    assert changes[0].field == "dataset"


def test_evaluators_change_is_detected():
    from_run = _run(evaluators=["CoherenceEvaluator"])
    to_run = _run(evaluators=["CoherenceEvaluator", "FluencyEvaluator"])

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    assert len(changes) == 1
    assert changes[0].field == "evaluators"


def test_thresholds_change_is_detected():
    from_run = _run(thresholds={"accuracy": {"min": 0.8}})
    to_run = _run(thresholds={"accuracy": {"min": 0.9}})

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    assert len(changes) == 1
    assert changes[0].field == "thresholds"


def test_multiple_simultaneous_changes_are_all_listed():
    from_run = _run(version="3", deployment="gpt-4o")
    to_run = _run(version="4", deployment="gpt-4o-mini")

    changes = regression_insight.build_changed_inputs(from_run, to_run)

    fields = {c.field for c in changes}
    assert fields == {"system_prompt", "model"}


# ---------------------------------------------------------------------------
# build_regression_insight
# ---------------------------------------------------------------------------


def test_no_insight_when_from_commit_missing():
    from_run = _run(accuracy=0.91, commit=None)
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    assert regression_insight.build_regression_insight(from_run, to_run, metric="accuracy") is None


def test_no_insight_when_to_commit_missing():
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=None)

    assert regression_insight.build_regression_insight(from_run, to_run, metric="accuracy") is None


def test_no_insight_when_metric_missing_on_either_run():
    from_run = _run(commit=_commit("a" * 40))
    to_run = _run(commit=_commit("b" * 40))
    to_run.aggregate_metrics = {}

    assert regression_insight.build_regression_insight(from_run, to_run, metric="accuracy") is None


def test_insight_names_metric_before_after_and_changed_inputs():
    from_run = _run(
        version="3",
        deployment="gpt-4o",
        accuracy=0.91,
        commit=_commit("a" * 40, short_sha="aaaaaaa"),
    )
    to_run = _run(
        version="4",
        deployment="gpt-4o-mini",
        accuracy=0.79,
        commit=_commit("b" * 40, short_sha="bbbbbbb"),
    )

    insight = regression_insight.build_regression_insight(from_run, to_run, metric="accuracy")

    assert insight is not None
    assert insight.metric == "accuracy"
    assert insight.from_value == 0.91
    assert insight.to_value == 0.79
    assert "0.91" in insight.explanation
    assert "0.79" in insight.explanation
    assert "aaaaaaa" in insight.explanation
    assert "bbbbbbb" in insight.explanation
    assert "prompt" in insight.explanation
    assert "gpt-4o" in insight.explanation and "gpt-4o-mini" in insight.explanation
    assert insight.suggested_action is not None
    assert len(insight.changed_inputs) == 2


def test_insight_explanation_has_no_cause_when_nothing_tracked_changed():
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40, short_sha="aaaaaaa"))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40, short_sha="bbbbbbb"))

    insight = regression_insight.build_regression_insight(from_run, to_run, metric="accuracy")

    assert insight is not None
    assert insight.changed_inputs == []
    assert "Likely cause" not in insight.explanation
    assert insight.suggested_action is None


def test_used_git_diff_true_when_both_commits_locally_resolvable(monkeypatch):
    monkeypatch.setattr(regression_insight, "commit_exists_locally", lambda sha, **kw: True)
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metric="accuracy")

    assert insight is not None
    assert insight.used_git_diff is True


def test_used_git_diff_false_when_commits_not_locally_resolvable(monkeypatch):
    monkeypatch.setattr(regression_insight, "commit_exists_locally", lambda sha, **kw: False)
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metric="accuracy")

    assert insight is not None
    assert insight.used_git_diff is False
