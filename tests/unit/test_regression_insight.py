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
    metrics: dict[str, float] | None = None,
    commit: CommitInfo | None = None,
    cloud_evaluation: dict | None = None,
) -> RunResult:
    aggregate_metrics = {"accuracy": accuracy}
    if metrics is not None:
        aggregate_metrics.update(metrics)
    config: dict = {"thresholds": thresholds or {}}
    if cloud_evaluation is not None:
        config["cloud_evaluation"] = cloud_evaluation
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
        aggregate_metrics=aggregate_metrics,
        summary=RunSummary(
            items_total=1,
            items_passed_all=1,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
        config=config,
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

    assert regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"]) is None


def test_no_insight_when_to_commit_missing():
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=None)

    assert regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"]) is None


def test_no_insight_when_agent_identity_differs():
    """FR-005: two runs of different agents aren't comparable for causal
    attribution, even with commit metadata and an apparent metric drop -
    an explicit --baseline file has no guarantee it points at the same
    agent, unlike Doctor's own previous_run selection (lineage-filtered by
    construction)."""
    from_run = _run(name="greeter", accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(name="other-agent", accuracy=0.79, commit=_commit("b" * 40))

    assert regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"]) is None


def test_dataset_change_is_attributed_not_blocked():
    """FR-006/build_changed_inputs explicitly attributes a regression to a
    dataset change - same_lineage must not require an identical
    dataset_path, both because that's a real, attributable cause this
    feature exists to surface, and because dataset_path isn't
    checkout-stable (local runs persist an absolute, resolved path), so a
    committed baseline from a different checkout than the current CI run
    would otherwise never match even for the literal same repo-relative
    dataset."""
    from_run = _run(dataset_path="data/a.jsonl", accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(dataset_path="data/b.jsonl", accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert any(c.field == "dataset" for c in insight.changed_inputs)


def test_evaluator_set_change_is_attributed_not_blocked():
    from_run = _run(
        evaluators=["CoherenceEvaluator"], accuracy=0.91, commit=_commit("a" * 40)
    )
    to_run = _run(
        evaluators=["CoherenceEvaluator", "FluencyEvaluator"],
        accuracy=0.79,
        commit=_commit("b" * 40),
    )

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert any(c.field == "evaluators" for c in insight.changed_inputs)


def test_same_lineage_is_version_blind():
    """A version/deployment bump alone must not count as a lineage
    mismatch - that's exactly the change this feature exists to attribute
    a regression to."""
    from_run = _run(version="3", deployment="gpt-4o", commit=_commit("a" * 40))
    to_run = _run(version="4", deployment="gpt-4o-mini", commit=_commit("b" * 40))

    assert regression_insight.same_lineage(from_run, to_run) is True


def test_normalize_agent_url_strips_only_the_version_segment():
    url = "https://acct.services.ai.azure.com/api/projects/p/agents/bot/versions/11"
    normalized = regression_insight.normalize_agent_url(url)

    assert "/versions/11" not in normalized
    assert normalized == "https://acct.services.ai.azure.com/api/projects/p/agents/bot"


def test_hosted_agent_same_lineage_across_a_version_bump():
    """A foundry_hosted URL embeds /versions/<version> - using it as-is for
    agent identity would make every version bump start a new lineage,
    exactly the change this feature exists to attribute a regression to."""
    base_url = "https://acct.services.ai.azure.com/api/projects/p/agents/bot/versions"
    from_run = _run(commit=_commit("a" * 40))
    from_run.target.kind = "foundry_hosted"
    from_run.target.url = f"{base_url}/11"
    to_run = _run(commit=_commit("b" * 40))
    to_run.target.kind = "foundry_hosted"
    to_run.target.url = f"{base_url}/12"

    assert regression_insight.same_lineage(from_run, to_run) is True


def test_hosted_agent_different_project_is_different_lineage():
    from_run = _run(commit=_commit("a" * 40))
    from_run.target.kind = "foundry_hosted"
    from_run.target.url = (
        "https://acct.services.ai.azure.com/api/projects/project-a/agents/bot/versions/1"
    )
    to_run = _run(commit=_commit("b" * 40))
    to_run.target.kind = "foundry_hosted"
    to_run.target.url = (
        "https://acct.services.ai.azure.com/api/projects/project-b/agents/bot/versions/1"
    )

    assert regression_insight.same_lineage(from_run, to_run) is False


def test_no_insight_when_metric_missing_on_either_run():
    from_run = _run(commit=_commit("a" * 40))
    to_run = _run(commit=_commit("b" * 40))
    to_run.aggregate_metrics = {}

    assert regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"]) is None


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

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert len(insight.regressed_metrics) == 1
    assert insight.regressed_metrics[0].metric == "accuracy"
    assert insight.regressed_metrics[0].from_value == 0.91
    assert insight.regressed_metrics[0].to_value == 0.79
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

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert insight.changed_inputs == []
    assert "Likely cause" not in insight.explanation
    assert insight.suggested_action is None


def test_commits_available_locally_true_when_both_commits_locally_resolvable(monkeypatch):
    monkeypatch.setattr(regression_insight, "commit_exists_locally", lambda sha, **kw: True)
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert insight.commits_available_locally is True


def test_commits_available_locally_false_when_commits_not_locally_resolvable(monkeypatch):
    monkeypatch.setattr(regression_insight, "commit_exists_locally", lambda sha, **kw: False)
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert insight.commits_available_locally is False


# ---------------------------------------------------------------------------
# Multiple regressed metrics (not just the worst one)
# ---------------------------------------------------------------------------


def test_insight_lists_all_three_regressed_metrics_ordered_worst_first():
    """similarity, coherence, and avg_latency_seconds all regress together -
    every one of them must appear in `regressed_metrics`, not just the
    single worst one, and ordering must be direction-aware (latency rising
    is a regression, not an improvement)."""
    from_run = _run(
        accuracy=0.91,
        metrics={"similarity": 4.0, "coherence": 4.5, "avg_latency_seconds": 2.0},
        commit=_commit("a" * 40),
    )
    to_run = _run(
        accuracy=0.91,
        # similarity: -25%, coherence: -56%, latency: +150% (all regressions)
        metrics={"similarity": 3.0, "coherence": 2.0, "avg_latency_seconds": 5.0},
        commit=_commit("b" * 40),
    )

    insight = regression_insight.build_regression_insight(
        from_run, to_run, metrics=["similarity", "coherence", "avg_latency_seconds"]
    )

    assert insight is not None
    assert [m.metric for m in insight.regressed_metrics] == [
        "avg_latency_seconds",
        "coherence",
        "similarity",
    ]
    assert "similarity" in insight.explanation
    assert "coherence" in insight.explanation
    assert "avg_latency_seconds" in insight.explanation
    assert "increased from 2.00 to 5.00" in insight.explanation


def test_insight_skips_only_the_metric_missing_a_value_not_the_whole_insight():
    """`coherence` is missing on `to_run` - the insight should still cover
    `similarity`, the metric that does have values on both sides, rather
    than bailing out entirely."""
    from_run = _run(
        accuracy=0.91,
        metrics={"similarity": 4.0, "coherence": 4.5},
        commit=_commit("a" * 40),
    )
    to_run = _run(
        accuracy=0.91,
        metrics={"similarity": 3.0},
        commit=_commit("b" * 40),
    )

    insight = regression_insight.build_regression_insight(
        from_run, to_run, metrics=["similarity", "coherence"]
    )

    assert insight is not None
    assert [m.metric for m in insight.regressed_metrics] == ["similarity"]


# ---------------------------------------------------------------------------
# regression_severity
# ---------------------------------------------------------------------------


def test_regression_severity_is_direction_aware_for_latency():
    # Latency rising from 4 to 8 is a 100% regression.
    assert regression_insight.regression_severity("avg_latency_seconds", 4.0, 8.0) == 1.0
    # Latency falling is an improvement, not a regression - severity is 0.
    assert regression_insight.regression_severity("avg_latency_seconds", 4.0, 2.0) == 0.0


def test_regression_severity_uses_absolute_change_when_from_value_is_zero_or_negative():
    assert regression_insight.regression_severity("coherence", 0.0, -5.0) == 5.0
    assert regression_insight.regression_severity("coherence", -1.0, -2.0) == 1.0


# ---------------------------------------------------------------------------
# report_url (Foundry Evaluations deep-link)
# ---------------------------------------------------------------------------


def test_report_urls_carried_through_when_provided():
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(
        from_run,
        to_run,
        metrics=["accuracy"],
        from_report_url="https://ai.azure.com/foundry/from",
        to_report_url="https://ai.azure.com/foundry/to",
    )

    assert insight is not None
    assert insight.from_report_url == "https://ai.azure.com/foundry/from"
    assert insight.to_report_url == "https://ai.azure.com/foundry/to"


def test_report_urls_are_none_when_not_provided():
    from_run = _run(accuracy=0.91, commit=_commit("a" * 40))
    to_run = _run(accuracy=0.79, commit=_commit("b" * 40))

    insight = regression_insight.build_regression_insight(from_run, to_run, metrics=["accuracy"])

    assert insight is not None
    assert insight.from_report_url is None
    assert insight.to_report_url is None


def test_resolve_report_url_reads_from_run_config_first(tmp_path):
    run = _run(cloud_evaluation={"report_url": "https://ai.azure.com/foundry/run1"})

    assert (
        regression_insight.resolve_report_url(run)
        == "https://ai.azure.com/foundry/run1"
    )


def test_resolve_report_url_falls_back_to_sidecar_file(tmp_path):
    run = _run()  # no cloud_evaluation in config - as for a `publish: true` local run
    results_dir = tmp_path / "run1"
    results_dir.mkdir()
    (results_dir / "cloud_evaluation.json").write_text(
        '{"report_url": "https://ai.azure.com/foundry/classic"}', encoding="utf-8"
    )

    url = regression_insight.resolve_report_url(
        run, results_path=results_dir / "results.json"
    )

    assert url == "https://ai.azure.com/foundry/classic"


def test_resolve_report_url_is_none_when_never_published(tmp_path):
    run = _run()
    results_dir = tmp_path / "run1"
    results_dir.mkdir()

    url = regression_insight.resolve_report_url(
        run, results_path=results_dir / "results.json"
    )

    assert url is None
