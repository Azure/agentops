"""Tests for the current-surface azd evaluation adapter.

Covers quickstart scenarios T1.3 (command sequence, polling, parsing,
aggregation, raw artifacts) and the User Story 2 gate behavior. The azd
boundary is faked throughout, so nothing here needs azd, Azure credentials,
or network access.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentops.core.agentops_config import AgentOpsConfig  # noqa: E402
from agentops.core.azd_eval import (  # noqa: E402
    current_recipe_metric_names,
    load_current_eval_recipe,
)
from agentops.pipeline import azd_eval_runner  # noqa: E402
from agentops.pipeline.azd_runner import AzdBackendError  # noqa: E402
from fixtures.azd_stub import AzdStub, arg_after, extension_list_json  # noqa: E402


CURRENT = "azure.ai.evaluations"

RECIPE = """
datasets:
  - name: smoke
    file: ./datasets/smoke.jsonl

evaluators:
  - name: travel-quality
    definition:
      type: rubric
      dimensions:
        - id: accuracy
          weight: 5
          description: Factually correct.

evals:
  - name: travel-regression
    dataset: smoke
    evaluators:
      - evaluator: builtin.task_adherence
      - evaluator: travel-quality
    target:
      type: agent
      name: travel-agent
""".lstrip()


def _workspace(tmp_path: Path, thresholds: dict[str, str] | None = None):
    recipe_path = tmp_path / "evals" / "azure.eval.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(RECIPE, encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(json.dumps({"input": "hi", "expected": "hello"}) + "\n", encoding="utf-8")

    config = AgentOpsConfig(
        version=1,
        agent="travel-agent:1",
        dataset=dataset,
        execution="azd",
        thresholds=thresholds or {},
    )
    recipe = load_current_eval_recipe(recipe_path)
    return recipe_path, recipe, config


def _run_payload(status: str = "completed", **extra):
    payload = {
        "id": "run-1",
        "eval_id": "eval-1",
        "status": status,
        "result_counts": {"total": 2, "passed": 2, "failed": 0, "errored": 0, "skipped": 0},
        "error": {"code": None, "message": None},
        "report_url": "https://ai.azure.com/report",
        "portal_url": "https://ai.azure.com/portal",
    }
    payload.update(extra)
    return payload


def _item(index: int, *, status="passed", results=None, data=None):
    return {
        "id": f"item-{index}",
        "run_id": "run-1",
        "status": status,
        "datasource_item": data
        if data is not None
        else {"input": f"q{index}", "expected": f"e{index}", "response": f"r{index}"},
        "results": results
        if results is not None
        else [
            {
                "name": "builtin.task_adherence",
                "metric": "builtin.task_adherence",
                "score": 4,
                "passed": True,
                "reason": "adhered",
            },
            {"name": "accuracy", "metric": "accuracy", "score": 5, "passed": True},
        ],
    }


def _script(stub: AzdStub, *, statuses=("completed",), items=None, run_payload=None):
    stub.expect("azd", "version")
    stub.expect(
        "extension", "list", "--installed", stdout=extension_list_json(CURRENT)
    )
    stub.expect("ai", "eval", "create")
    stub.expect(
        "ai", "eval", "run", "start",
        stdout=json.dumps({"run_id": "run-1", "eval_id": "eval-1", "status": "queued"}),
    )
    for status in statuses:
        payload = run_payload if status == statuses[-1] and run_payload else _run_payload(status)
        stub.expect("ai", "eval", "run", "show", stdout=json.dumps(payload))
    if items is not None:
        stub.expect_output_file("ai", "eval", "run", "output", "list", payload=items)
    return stub


def _execute(tmp_path, monkeypatch, stub, recipe_path, recipe, **kwargs):
    stub.install(monkeypatch)
    return azd_eval_runner.run_current_eval(
        recipe_path,
        recipe.primary_eval(),
        workspace=tmp_path,
        sleep=lambda _seconds: None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# T010 - command sequence and flag correctness
# ---------------------------------------------------------------------------


def test_command_sequence_matches_the_contract(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0), _item(1)])

    run = _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    stub.assert_exhausted()
    assert run.run_id == "run-1"
    assert run.eval_id == "eval-1"
    assert run.status == "completed"
    assert len(run.output_items) == 2

    ordered = [argv[2:5] for argv in stub.calls if argv[1] == "--no-prompt"]
    assert ordered == [
        ["ai", "eval", "create"],
        ["ai", "eval", "run"],
        ["ai", "eval", "run"],
        ["ai", "eval", "run"],
    ]


def test_path_flag_receives_the_recipe_directory_not_the_file(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])

    _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    for argv in stub.find("--path"):
        assert arg_after(argv, "--path") == str(recipe_path.parent)


def test_run_start_names_the_eval_by_flag_and_never_positionally(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])

    _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    start = stub.find("run", "start")[0]
    assert arg_after(start, "--eval") == "travel-regression"
    # Upstream rejects a positional here; the token right after `start` must be a flag.
    assert start[start.index("start") + 1].startswith("--")
    assert "--no-wait" in start


def test_output_list_takes_the_run_id_positionally_and_defeats_paging(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])

    _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    listed = stub.find("output", "list")[0]
    assert listed[listed.index("list") + 1] == "run-1"
    assert "--all" in listed
    assert "--output-file" in listed
    # The current surface spells this --output-file; --out-file is the legacy flag.
    assert "--out-file" not in listed


def test_no_failure_gating_flag_is_ever_passed(tmp_path: Path, monkeypatch) -> None:
    """AgentOps owns the gate; delegating it would collapse exit-code meaning."""

    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])

    _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    stub.assert_no_call_containing("--fail-on")


def test_missing_extension_is_an_actionable_configuration_error(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = AzdStub()
    stub.expect("azd", "version")
    stub.expect("extension", "list", "--installed", stdout=extension_list_json("azure.ai.agents"))

    with pytest.raises(AzdBackendError) as excinfo:
        _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    message = str(excinfo.value)
    assert CURRENT in message
    assert azd_eval_runner.CURRENT_MIN_AZD_VERSION in message
    assert "azure.ai.agents" not in message.replace(CURRENT, "")
    # No evaluation may be created when the surface is unusable.
    stub.assert_no_call_containing("ai", "eval", "create")


# ---------------------------------------------------------------------------
# T011 - polling and terminal statuses
# ---------------------------------------------------------------------------


def test_polling_continues_until_the_run_completes(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(
        AzdStub(),
        statuses=("queued", "running", "completed"),
        items=[_item(0)],
    )

    run = _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    assert run.status == "completed"
    assert len(stub.find("run", "show")) == 3


def test_unrecognized_status_is_treated_as_non_terminal(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(
        AzdStub(),
        statuses=("mystery-state", "completed"),
        items=[_item(0)],
    )

    run = _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    assert run.status == "completed"
    assert len(stub.find("run", "show")) == 2


@pytest.mark.parametrize("status", ["failed", "error", "canceled", "cancelled"])
def test_terminal_failure_statuses_raise_a_runtime_error(
    tmp_path: Path, monkeypatch, status: str
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), statuses=(status,))

    with pytest.raises(AzdBackendError) as excinfo:
        _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    message = str(excinfo.value)
    assert status in message
    assert "run-1" in message
    assert "eval-1" in message


def test_timeout_preserves_the_identifiers_and_last_status(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), statuses=("running",))

    with pytest.raises(AzdBackendError) as excinfo:
        _execute(
            tmp_path, monkeypatch, stub, recipe_path, recipe, timeout_seconds=0.0
        )

    message = str(excinfo.value)
    assert "run-1" in message
    assert "eval-1" in message
    assert "running" in message
    assert "terminal state" in message


def test_start_without_a_run_identifier_fails_fast(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = AzdStub()
    stub.expect("azd", "version")
    stub.expect("extension", "list", "--installed", stdout=extension_list_json(CURRENT))
    stub.expect("ai", "eval", "create")
    stub.expect("ai", "eval", "run", "start", stdout=json.dumps({"status": "queued"}))

    with pytest.raises(AzdBackendError, match="did not return a run identifier"):
        _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)


# ---------------------------------------------------------------------------
# T012 - parsing, aggregation, tolerant decoding, three-valued verdicts
# ---------------------------------------------------------------------------


def _normalize(tmp_path: Path, items, *, thresholds=None, status="completed", run_payload=None):
    recipe_path, recipe, config = _workspace(tmp_path, thresholds)
    declared = current_recipe_metric_names(recipe, recipe_path)
    binding = azd_eval_runner.preflight_bind_thresholds(config.thresholds.keys(), declared)
    payload = run_payload or _run_payload(status)
    run = azd_eval_runner.CurrentEvalRun(
        recipe_path=recipe_path,
        run_id="run-1",
        status=status,
        run_payload=payload,
        output_items=tuple(items),
        eval_id="eval-1",
        eval_name="travel-regression",
        report_url="https://ai.azure.com/report",
        error_message=azd_eval_runner._run_error_message(payload),
        duration_seconds=1.0,
    )
    return azd_eval_runner.normalize_to_results(
        run,
        config=config,
        recipe=recipe,
        evaluation=recipe.primary_eval(),
        metric_binding=binding,
        started_at=datetime.now(timezone.utc),
    )


def test_aggregate_metric_is_the_mean_of_present_scores(tmp_path: Path) -> None:
    items = [
        _item(0, results=[{"metric": "accuracy", "score": 4, "passed": True}]),
        _item(1, results=[{"metric": "accuracy", "score": 2, "passed": False}]),
    ]

    result = _normalize(tmp_path, items)

    assert result.aggregate_metrics["accuracy"] == 3.0


def test_absent_score_is_excluded_rather_than_counted_as_zero(tmp_path: Path) -> None:
    items = [
        _item(0, results=[{"metric": "accuracy", "score": 4, "passed": True}]),
        _item(1, results=[{"metric": "accuracy", "score": None, "passed": None}]),
    ]

    result = _normalize(tmp_path, items)

    # A zero would drag the mean to 2.0 and fail a >=3 gate on evidence that
    # was never actually produced.
    assert result.aggregate_metrics["accuracy"] == 4.0


def test_metric_with_no_scores_anywhere_is_absent_from_the_aggregate(
    tmp_path: Path,
) -> None:
    items = [_item(0, results=[{"metric": "accuracy", "score": "not-a-number"}])]

    result = _normalize(tmp_path, items)

    assert "accuracy" not in result.aggregate_metrics


def test_score_arriving_as_a_string_is_decoded(tmp_path: Path) -> None:
    items = [_item(0, results=[{"metric": "accuracy", "score": "4", "passed": True}])]

    result = _normalize(tmp_path, items)

    assert result.aggregate_metrics["accuracy"] == 4.0


def test_null_verdict_is_neither_a_pass_nor_a_judged_failure(tmp_path: Path) -> None:
    unjudged = azd_eval_runner.parse_output_item(
        0, _item(0, status="", results=[{"metric": "accuracy", "score": 4, "passed": None}])
    )

    assert unjudged.scores[0].passed is None
    assert unjudged.outcome == "failed"  # not an explicit pass, so it fails closed


def test_sample_with_an_empty_result_list_is_a_failure(tmp_path: Path) -> None:
    sample = azd_eval_runner.parse_output_item(0, _item(0, status="", results=[]))

    assert sample.outcome == "failed"


def test_declared_status_wins_over_derivation(tmp_path: Path) -> None:
    sample = azd_eval_runner.parse_output_item(0, _item(0, status="errored", results=[]))

    assert sample.outcome == "errored"


def test_null_run_error_members_are_not_a_failure(tmp_path: Path) -> None:
    payload = _run_payload("completed", error={"code": None, "message": None})

    assert azd_eval_runner._run_error_message(payload) is None


def test_non_empty_run_error_message_is_a_failure(tmp_path: Path) -> None:
    payload = _run_payload("completed", error={"code": "QuotaExceeded", "message": "no capacity"})

    detail = azd_eval_runner._run_error_message(payload)

    assert detail is not None
    assert "QuotaExceeded" in detail and "no capacity" in detail


def test_report_url_wins_over_portal_url(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])

    run = _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    assert run.report_url == "https://ai.azure.com/report"


def test_rows_are_populated_one_per_sample(tmp_path: Path) -> None:
    result = _normalize(tmp_path, [_item(0), _item(1)])

    assert len(result.rows) == 2
    assert result.rows[0].input == "q0"
    assert result.rows[0].expected == "e0"
    assert result.rows[0].response == "r0"
    assert {metric.name for metric in result.rows[0].metrics} == {
        "builtin.task_adherence",
        "accuracy",
    }
    assert result.config["result_granularity"] == "row"
    assert result.config["azd_evaluation"]["surface"] == "current"
    assert result.config["azd_evaluation"]["extension"] == CURRENT


def test_errored_samples_are_kept_as_rows_with_an_error(tmp_path: Path) -> None:
    items = [_item(0), _item(1, status="errored", results=[])]
    payload = _run_payload(
        "completed",
        result_counts={"total": 2, "passed": 1, "failed": 0, "errored": 1, "skipped": 0},
    )

    result = _normalize(tmp_path, items, run_payload=payload)

    assert len(result.rows) == 2
    assert result.rows[1].error is not None
    assert result.summary.items_total == 2
    assert result.config["azd_evaluation"]["result_counts"]["errored"] == 1


def test_partial_retrieval_cannot_inflate_the_pass_rate(tmp_path: Path) -> None:
    payload = _run_payload(
        "completed",
        result_counts={"total": 10, "passed": 10, "failed": 0, "errored": 0, "skipped": 0},
    )

    result = _normalize(tmp_path, [_item(0)], run_payload=payload)

    assert result.summary.items_passed_all == 1
    assert result.config["azd_evaluation"]["retrieval_warnings"]


# ---------------------------------------------------------------------------
# T013 - raw artifacts
# ---------------------------------------------------------------------------


def test_raw_artifacts_are_written_for_a_successful_run(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    stub = _script(AzdStub(), items=[_item(0)])
    run = _execute(tmp_path, monkeypatch, stub, recipe_path, recipe)

    out = tmp_path / "out"
    azd_eval_runner.write_raw_artifacts(run, out)

    assert json.loads((out / "azd_evaluation.json").read_text(encoding="utf-8"))["id"] == "run-1"
    assert len(json.loads((out / "azd_eval_output_items.json").read_text(encoding="utf-8"))) == 1
    assert (out / "azd_stdout.log").exists()


def test_raw_artifacts_are_written_when_the_run_fails(tmp_path: Path, monkeypatch) -> None:
    """T037: a failed run stays diagnosable without re-running the evaluation."""

    recipe_path, recipe, _ = _workspace(tmp_path)
    out = tmp_path / "out"
    stub = _script(AzdStub(), statuses=("failed",))

    with pytest.raises(AzdBackendError):
        _execute(tmp_path, monkeypatch, stub, recipe_path, recipe, debug_dir=out)

    payload = json.loads((out / "azd_evaluation.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert (out / "azd_eval_output_items.json").exists()


def test_raw_artifacts_are_written_when_retrieval_fails(tmp_path: Path, monkeypatch) -> None:
    recipe_path, recipe, _ = _workspace(tmp_path)
    out = tmp_path / "out"
    stub = _script(AzdStub())
    stub.expect(
        "ai", "eval", "run", "output", "list",
        returncode=1,
        stderr="listing blew up",
    )

    with pytest.raises(AzdBackendError):
        _execute(tmp_path, monkeypatch, stub, recipe_path, recipe, debug_dir=out)

    assert (out / "azd_evaluation.json").exists()


# ---------------------------------------------------------------------------
# T022 - pre-flight threshold binding (no azd invocation)
# ---------------------------------------------------------------------------


def test_unmatched_threshold_is_a_configuration_error_before_any_azd_call(
    tmp_path: Path, monkeypatch
) -> None:
    recipe_path, recipe, config = _workspace(tmp_path, {"not_a_metric": ">=3"})
    stub = AzdStub().install(monkeypatch)
    declared = current_recipe_metric_names(recipe, recipe_path)

    with pytest.raises(AzdBackendError) as excinfo:
        azd_eval_runner.preflight_bind_thresholds(config.thresholds.keys(), declared)

    message = str(excinfo.value)
    assert "not_a_metric" in message
    assert "no cloud run was consumed" in message.lower()
    stub.assert_never_invoked()


def test_ambiguous_threshold_is_a_configuration_error_before_any_azd_call(
    tmp_path: Path, monkeypatch
) -> None:
    stub = AzdStub().install(monkeypatch)

    with pytest.raises(AzdBackendError, match="ambiguous"):
        # Both metrics alias to the bare suffix "coherence", so the threshold
        # key cannot be bound to exactly one of them.
        azd_eval_runner.preflight_bind_thresholds(
            ["coherence"], ["builtin.coherence", "rubric.coherence"]
        )

    stub.assert_never_invoked()


def test_threshold_binds_to_a_builtin_via_the_narrow_alias_rule(tmp_path: Path) -> None:
    recipe_path, recipe, config = _workspace(tmp_path, {"task_adherence": ">=3"})
    declared = current_recipe_metric_names(recipe, recipe_path)

    binding = azd_eval_runner.preflight_bind_thresholds(config.thresholds.keys(), declared)

    assert binding.bound == {"task_adherence": "builtin.task_adherence"}


# ---------------------------------------------------------------------------
# T023/T024 - post-run gate behavior
# ---------------------------------------------------------------------------


def test_satisfied_threshold_passes(tmp_path: Path) -> None:
    result = _normalize(tmp_path, [_item(0), _item(1)], thresholds={"accuracy": ">=4"})

    assert result.summary.overall_passed is True


def test_unsatisfied_threshold_fails_the_gate(tmp_path: Path) -> None:
    result = _normalize(tmp_path, [_item(0), _item(1)], thresholds={"accuracy": ">=5.5"})

    assert result.summary.overall_passed is False


def test_declared_metric_the_run_never_emitted_fails_closed(tmp_path: Path) -> None:
    """Declared but unemitted is a gate failure, not a crash and not a pass."""

    items = [_item(0, results=[{"metric": "builtin.task_adherence", "score": 5, "passed": True}])]

    result = _normalize(tmp_path, items, thresholds={"accuracy": ">=1"})

    assert result.summary.overall_passed is False
    failed = [item for item in result.thresholds if not item.passed]
    assert [item.metric for item in failed] == ["accuracy"]
    assert failed[0].actual == "missing"
    assert result.config["azd_evaluation"]["missing_metrics"] == ["accuracy"]


def test_rubric_dimension_thresholds_gate_like_builtin_metrics(tmp_path: Path) -> None:
    result = _normalize(
        tmp_path,
        [_item(0), _item(1)],
        thresholds={"accuracy": ">=5", "task_adherence": ">=4"},
    )

    assert result.summary.overall_passed is True
    assert {item.metric for item in result.thresholds} == {"accuracy", "task_adherence"}


def test_zero_samples_never_reports_a_pass(tmp_path: Path) -> None:
    payload = _run_payload(
        "completed",
        result_counts={"total": 0, "passed": 0, "failed": 0, "errored": 0, "skipped": 0},
    )

    result = _normalize(tmp_path, [], run_payload=payload)

    assert result.summary.overall_passed is False


def test_no_decodable_metrics_never_reports_a_pass(tmp_path: Path) -> None:
    items = [_item(0, results=[{"metric": "accuracy", "score": "bogus"}])]

    result = _normalize(tmp_path, items)

    assert result.aggregate_metrics == {}
    assert result.summary.overall_passed is False


def test_run_level_error_message_prevents_a_pass(tmp_path: Path) -> None:
    payload = _run_payload("completed", error={"code": "Partial", "message": "degraded"})

    result = _normalize(tmp_path, [_item(0)], run_payload=payload)

    assert result.summary.overall_passed is False
    assert result.config["azd_evaluation"]["error_message"] is not None
