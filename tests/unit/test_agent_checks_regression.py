"""Tests for the regression check."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentops.agent.checks.regression import run_regression_check
from agentops.agent.config import RegressionCheckConfig
from agentops.agent.findings import Severity
from agentops.agent.sources.results_history import ResultsHistory, RunSummary


def _run(
    metrics: dict,
    run_id: str = "r",
    offset_days: int = 0,
    fingerprint: str | None = None,
    lineage_key: str | None = None,
    agent_identity_key: str | None = None,
    raw_path: Path | None = None,
    source: str = "local",
) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc) + timedelta(days=offset_days),
        metrics=metrics,
        run_pass=True,
        items_total=1,
        items_passed_all=1,
        raw_path=raw_path or Path("dummy"),
        methodology_fingerprint=fingerprint,
        lineage_key=lineage_key,
        agent_identity_key=agent_identity_key,
        source=source,
    )


def _write_result_json(
    path: Path,
    *,
    accuracy: float,
    version: str,
    deployment: str,
    commit_sha: str,
    dataset_path: str = "data/smoke.jsonl",
) -> None:
    payload = {
        "version": 1,
        "started_at": "2026-09-01T10:00:00+00:00",
        "finished_at": "2026-09-01T10:00:01+00:00",
        "duration_seconds": 1.0,
        "target": {
            "kind": "foundry_prompt",
            "raw": f"greeter:{version}",
            "name": "greeter",
            "version": version,
            "deployment": deployment,
        },
        "dataset_path": dataset_path,
        "evaluators": ["CoherenceEvaluator"],
        "rows": [],
        "aggregate_metrics": {"accuracy": accuracy},
        "thresholds": [],
        "summary": {
            "items_total": 1,
            "items_passed_all": 1,
            "items_pass_rate": 1.0,
            "thresholds_total": 0,
            "thresholds_passed": 0,
            "threshold_pass_rate": 1.0,
            "overall_passed": True,
        },
        "config": {},
        "commit": {
            "sha": commit_sha,
            "short_sha": commit_sha[:7],
            "subject": "A commit",
            "author": "Dev",
            "authored_at": "2026-09-01T10:00:00+00:00",
            "source": "ci",
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_regression_check_flags_drop_above_threshold() -> None:
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert findings[0].id == "regression.coherence"
    # Drop is ~33% which is >= 2*threshold (20%) -> CRITICAL.
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].evidence["latest_run_id"] == "latest"


def test_regression_check_ignores_small_drops() -> None:
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 4.4}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_skips_when_baseline_too_small() -> None:
    history = ResultsHistory(runs=[_run({"coherence": 4.5}, run_id="only")])
    config = RegressionCheckConfig(metrics=["coherence"], min_runs=3)
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_ignores_baselines_with_mismatched_methodology() -> None:
    """Baselines from a different dataset/evaluator set must not count.
    Detection (`baseline_runs`) stays on the strict, version-inclusive
    `methodology_fingerprint` - unchanged by this feature; only picking
    which run to *explain* an already-detected regression against uses the
    coarser `lineage_key` (see
    ``test_attribution_uses_the_true_immediately_preceding_run_across_a_version_change``
    below)."""
    history = ResultsHistory(
        runs=[
            # These baselines used a different methodology (e.g. smoke dataset)
            # and must be excluded from the comparison.
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3, fingerprint="A"),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2, fingerprint="A"),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0, fingerprint="B"),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert findings == []


def test_regression_check_uses_matching_methodology_baselines() -> None:
    """Baselines with the same fingerprint as the latest run drive the check."""
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="other", offset_days=-4, fingerprint="A"),
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3, fingerprint="B"),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2, fingerprint="B"),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0, fingerprint="B"),
        ]
    )
    config = RegressionCheckConfig(
        metrics=["coherence"], threshold_drop=0.10, min_runs=3
    )
    findings = run_regression_check(history, config)
    assert len(findings) == 1
    assert findings[0].evidence["baseline_runs"] == 2


def test_regression_check_attaches_insight_when_both_runs_have_commit_metadata(tmp_path) -> None:
    baseline_path = tmp_path / "baseline" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.79, version="4", deployment="gpt-4o-mini", commit_sha="b" * 40
    )
    history = ResultsHistory(
        runs=[
            _run({"accuracy": 0.91}, run_id="b1", offset_days=-3, raw_path=baseline_path),
            _run({"accuracy": 0.91}, run_id="b2", offset_days=-2, raw_path=baseline_path),
            _run({"accuracy": 0.79}, run_id="latest", offset_days=0, raw_path=latest_path),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    finding = findings[0]
    assert "insight" in finding.evidence
    assert finding.evidence["insight"]["regressed_metrics"][0]["metric"] == "accuracy"
    assert "gpt-4o" in finding.recommendation and "gpt-4o-mini" in finding.recommendation
    assert "prompt" in finding.recommendation


def test_regression_check_insight_carries_report_url_when_published_present_and_absent(
    tmp_path,
) -> None:
    """The baseline run was published (sidecar ``cloud_evaluation.json``
    next to its ``results.json``); the latest run was not - the insight
    must carry the former's URL and omit the latter's, rather than
    fabricating or erroring on the missing side."""
    baseline_path = tmp_path / "baseline" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    (baseline_path.parent / "cloud_evaluation.json").write_text(
        '{"report_url": "https://ai.azure.com/foundry/baseline"}', encoding="utf-8"
    )
    _write_result_json(
        latest_path, accuracy=0.79, version="4", deployment="gpt-4o-mini", commit_sha="b" * 40
    )
    history = ResultsHistory(
        runs=[
            _run({"accuracy": 0.91}, run_id="b1", offset_days=-3, raw_path=baseline_path),
            _run({"accuracy": 0.91}, run_id="b2", offset_days=-2, raw_path=baseline_path),
            _run({"accuracy": 0.79}, run_id="latest", offset_days=0, raw_path=latest_path),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    insight = findings[0].evidence["insight"]
    assert insight["from_report_url"] == "https://ai.azure.com/foundry/baseline"
    assert insight["to_report_url"] is None


def test_regression_check_keeps_generic_recommendation_without_commit_metadata() -> None:
    """Today's behavior is unchanged when raw_path can't be reloaded (e.g. missing file)."""
    history = ResultsHistory(
        runs=[
            _run({"coherence": 4.5}, run_id="b1", offset_days=-3),
            _run({"coherence": 4.5}, run_id="b2", offset_days=-2),
            _run({"coherence": 3.0}, run_id="latest", offset_days=0),
        ]
    )
    config = RegressionCheckConfig(metrics=["coherence"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert "insight" not in findings[0].evidence
    assert "inspect prompt/model/dataset changes" in findings[0].recommendation


def test_regression_check_explains_why_attribution_is_unavailable_for_cloud_sourced_runs(
    tmp_path,
) -> None:
    baseline_path = tmp_path / "baseline" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.79, version="4", deployment="gpt-4o-mini", commit_sha="b" * 40
    )
    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.91},
                run_id="b1",
                offset_days=-3,
                raw_path=baseline_path,
                source="foundry_cloud",
            ),
            _run(
                {"accuracy": 0.91},
                run_id="b2",
                offset_days=-2,
                raw_path=baseline_path,
                source="foundry_cloud",
            ),
            _run(
                {"accuracy": 0.79},
                run_id="latest",
                offset_days=0,
                raw_path=latest_path,
                source="foundry_cloud",
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert "insight" not in findings[0].evidence
    assert findings[0].evidence["attribution_unavailable"] == (
        "attribution unavailable: run has no commit (fetched from cloud)"
    )
    assert "attribution unavailable: run has no commit (fetched from cloud)" in (
        findings[0].recommendation
    )


def test_regression_check_explains_unavailable_attribution_for_mixed_local_and_cloud_runs(
    tmp_path,
) -> None:
    """A local baseline compared against a cloud-fetched current run (the
    asymmetric case Doctor's history merge can produce) - no commit is
    available on the cloud side, so attribution is unavailable, and the
    finding must say why rather than silently falling back to the generic
    recommendation."""
    baseline_path = tmp_path / "baseline" / "results.json"
    _write_result_json(
        baseline_path, accuracy=0.91, version="3", deployment="gpt-4o", commit_sha="a" * 40
    )
    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.91},
                run_id="b1",
                offset_days=-3,
                raw_path=baseline_path,
                source="local",
            ),
            _run(
                {"accuracy": 0.91},
                run_id="b2",
                offset_days=-2,
                raw_path=baseline_path,
                source="local",
            ),
            _run(
                {"accuracy": 0.79},
                run_id="latest",
                offset_days=0,
                raw_path=Path("foundry") / "eval123" / "run456",
                source="foundry_cloud",
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    assert "insight" not in findings[0].evidence
    assert findings[0].evidence["attribution_unavailable"] == (
        "attribution unavailable: run has no commit (fetched from cloud)"
    )


def test_attribution_uses_the_true_immediately_preceding_run_across_a_version_change(
    tmp_path,
) -> None:
    """A one-off run on a different version (v5) sits between a v4 run and
    the latest v4 run. `previous_run` must be v5 - the run actually
    immediately before `latest` - not the older v4 run two steps back.
    """
    v4_path = tmp_path / "v4" / "results.json"
    v5_path = tmp_path / "v5" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        v4_path, accuracy=0.90, version="4", deployment="gpt-4o", commit_sha="1" * 40
    )
    _write_result_json(
        v5_path, accuracy=0.95, version="5", deployment="gpt-4o", commit_sha="2" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.70, version="4", deployment="gpt-4o", commit_sha="3" * 40
    )

    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.90},
                run_id="v4",
                offset_days=-2,
                fingerprint="V4",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=v4_path,
            ),
            _run(
                {"accuracy": 0.95},
                run_id="v5",
                offset_days=-1,
                fingerprint="V5",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=v5_path,
            ),
            _run(
                {"accuracy": 0.70},
                run_id="latest",
                offset_days=0,
                fingerprint="V4",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=latest_path,
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=2)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    insight = findings[0].evidence["insight"]
    # The v5 run's commit, not the older v4 run's - confirms attribution
    # diffed against the true immediately-preceding run.
    assert insight["from_commit"]["sha"] == "2" * 40
    fields = {c["field"] for c in insight["changed_inputs"]}
    assert "system_prompt" in fields


def test_first_run_after_a_version_bump_is_a_known_accepted_gap_in_doctor() -> None:
    """Doctor's rolling-baseline detection (`baseline_runs`/`methodology_fingerprint`)
    is deliberately left untouched by this feature - changing *whether*/*how
    sensitively* it fires is a bigger, separate decision than "add a causal
    explanation to an already-detected regression", and would alter
    detection behavior for every Doctor user, not just this feature's own
    scenario. So the very first run after a version bump still can't fire
    here: no prior run shares the brand-new fingerprint yet, so
    `baseline_runs` is empty and the gate returns early - by design, not
    oversight. The canonical "run v3 -> v4" scenario from research.md is
    fully covered instead via the explicit `--baseline` comparison path
    (`pipeline.comparison`), which has no such gate.
    """
    history = ResultsHistory(
        runs=[
            _run({"accuracy": 0.90}, run_id="v3a", offset_days=-2, fingerprint="V3"),
            _run({"accuracy": 0.91}, run_id="v3b", offset_days=-1, fingerprint="V3"),
            _run({"accuracy": 0.70}, run_id="v4", offset_days=0, fingerprint="V4"),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=3)

    findings = run_regression_check(history, config)

    assert findings == []


def test_no_insight_when_the_selected_pair_did_not_itself_regress(tmp_path) -> None:
    """`previous_run` is selected independently of `baseline_runs` (by
    lineage, not fingerprint), so a metric can regress against the
    rolling baseline while actually *improving* between that specific
    pair: v4=0.90, then a one-off v5 excursion at 0.50, then v4=0.70 again
    - the rolling mean (0.90) vs 0.70 is a real ~22% drop that must still
    fire, but the immediately preceding run (v5, 0.50) to latest (0.70) is
    an *increase*, not a drop. Building an insight from that pair would
    describe an improvement as the regression's cause - it must be
    skipped, with a clear reason, instead.
    """
    v4_path = tmp_path / "v4" / "results.json"
    v5_path = tmp_path / "v5" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        v4_path, accuracy=0.90, version="4", deployment="gpt-4o", commit_sha="1" * 40
    )
    _write_result_json(
        v5_path, accuracy=0.50, version="5", deployment="gpt-4o", commit_sha="2" * 40
    )
    _write_result_json(
        latest_path, accuracy=0.70, version="4", deployment="gpt-4o", commit_sha="3" * 40
    )

    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.90},
                run_id="v4",
                offset_days=-2,
                fingerprint="V4",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=v4_path,
            ),
            _run(
                {"accuracy": 0.50},
                run_id="v5",
                offset_days=-1,
                fingerprint="V5",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=v5_path,
            ),
            _run(
                {"accuracy": 0.70},
                run_id="latest",
                offset_days=0,
                fingerprint="V4",
                lineage_key="L",
                agent_identity_key="L",
                raw_path=latest_path,
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=2)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    finding = findings[0]
    assert "insight" not in finding.evidence
    assert finding.evidence["attribution_unavailable"] == (
        "attribution unavailable: the immediately preceding comparable "
        "run didn't show this drop - likely driven by the broader rolling "
        "baseline instead"
    )
    assert "didn't show this drop" in finding.recommendation


def test_attribution_picks_the_immediately_preceding_run_across_a_dataset_change(
    tmp_path,
) -> None:
    """Older runs used dataset B, one intervening run used dataset A, then
    the latest (regressed) run is back on dataset B. Filtering candidates
    by `lineage_key` (which also requires the same dataset) would skip the
    dataset-A run and miss that the dataset changed in between -
    `agent_identity_key` (agent identity only) must pick it instead.
    """
    b1_path = tmp_path / "b1" / "results.json"
    a_path = tmp_path / "a" / "results.json"
    latest_path = tmp_path / "latest" / "results.json"
    _write_result_json(
        b1_path,
        accuracy=0.90,
        version="4",
        deployment="gpt-4o",
        commit_sha="1" * 40,
        dataset_path="data/dataset-b.jsonl",
    )
    _write_result_json(
        a_path,
        accuracy=0.92,
        version="4",
        deployment="gpt-4o",
        commit_sha="2" * 40,
        dataset_path="data/dataset-a.jsonl",
    )
    _write_result_json(
        latest_path,
        accuracy=0.70,
        version="4",
        deployment="gpt-4o",
        commit_sha="3" * 40,
        dataset_path="data/dataset-b.jsonl",
    )

    history = ResultsHistory(
        runs=[
            _run(
                {"accuracy": 0.90},
                run_id="b1",
                offset_days=-2,
                fingerprint="B",
                agent_identity_key="agent",
                raw_path=b1_path,
            ),
            _run(
                {"accuracy": 0.92},
                run_id="a",
                offset_days=-1,
                fingerprint="A",
                agent_identity_key="agent",
                raw_path=a_path,
            ),
            _run(
                {"accuracy": 0.70},
                run_id="latest",
                offset_days=0,
                fingerprint="B",
                agent_identity_key="agent",
                raw_path=latest_path,
            ),
        ]
    )
    config = RegressionCheckConfig(metrics=["accuracy"], threshold_drop=0.10, min_runs=2)

    findings = run_regression_check(history, config)

    assert len(findings) == 1
    insight = findings[0].evidence["insight"]
    # The dataset-A run's commit, not the older dataset-B run's.
    assert insight["from_commit"]["sha"] == "2" * 40
    fields = {c["field"] for c in insight["changed_inputs"]}
    assert "dataset" in fields
