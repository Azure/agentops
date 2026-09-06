"""End-to-end CLI coverage for the current azd evaluation surface.

Covers quickstart scenarios T1.5 (exit-code matrix) and T2-equivalents run
against a faked azd. Exit codes are a public contract, so every outcome is
asserted through the real CLI rather than by calling the adapter directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentops.cli.app import app  # noqa: E402
from fixtures.azd_stub import AzdStub, extension_list_json  # noqa: E402


runner = CliRunner()

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


def _workspace(tmp_path: Path, thresholds: str = "") -> Path:
    (tmp_path / "evals").mkdir(parents=True, exist_ok=True)
    (tmp_path / "evals" / "azure.eval.yaml").write_text(RECIPE, encoding="utf-8")
    (tmp_path / "dataset.jsonl").write_text(
        json.dumps({"input": "hi", "expected": "hello"}) + "\n", encoding="utf-8"
    )
    config_path = tmp_path / "agentops.yaml"
    config_path.write_text(
        "version: 1\n"
        "agent: travel-agent:1\n"
        "dataset: dataset.jsonl\n"
        "execution: azd\n" + thresholds,
        encoding="utf-8",
    )
    return config_path


def _run_payload(status: str = "completed", *, total: int = 2, passed: int = 2, **extra):
    payload = {
        "id": "run-1",
        "eval_id": "eval-1",
        "status": status,
        "result_counts": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "errored": 0,
            "skipped": 0,
        },
        "error": {"code": None, "message": None},
        "report_url": "https://ai.azure.com/report",
    }
    payload.update(extra)
    return payload


def _items(*scores: tuple[float, float]):
    return [
        {
            "id": f"item-{index}",
            "run_id": "run-1",
            "status": "passed",
            "datasource_item": {"input": f"q{index}", "expected": "e", "response": "r"},
            "results": [
                {"metric": "builtin.task_adherence", "score": adherence, "passed": True},
                {"metric": "accuracy", "score": accuracy, "passed": True},
            ],
        }
        for index, (adherence, accuracy) in enumerate(scores)
    ]


def _script(stub: AzdStub, *, statuses=("completed",), items=None, run_payload=None):
    stub.expect("azd", "version")
    stub.expect("extension", "list", "--installed", stdout=extension_list_json(CURRENT))
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


def _invoke(config_path: Path, out: Path, *extra: str):
    return runner.invoke(
        app,
        ["eval", "run", "--config", str(config_path), "--output", str(out), *extra],
    )


# ---------------------------------------------------------------------------
# T021 - happy path
# ---------------------------------------------------------------------------


def test_current_surface_run_produces_normalized_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _workspace(tmp_path, "thresholds:\n  accuracy: '>=4'\n")
    out = tmp_path / "out"
    stub = _script(AzdStub(), items=_items((4.0, 5.0), (4.0, 5.0))).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 0, result.output
    stub.assert_exhausted()
    payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert payload["summary"]["overall_passed"] is True
    assert payload["aggregate_metrics"]["accuracy"] == 5.0
    assert len(payload["rows"]) == 2
    assert payload["config"]["azd_evaluation"]["surface"] == "current"
    assert (out / "report.md").exists()
    assert (out / "azd_evaluation.json").exists()
    assert (out / "azd_eval_output_items.json").exists()


def test_the_evaluation_is_executed_exactly_once(tmp_path: Path, monkeypatch) -> None:
    config_path = _workspace(tmp_path)
    out = tmp_path / "out"
    stub = _script(AzdStub(), items=_items((4.0, 5.0))).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 0, result.output
    starts = [argv for argv in stub.calls if argv[2:6] == ["ai", "eval", "run", "start"]]
    assert len(starts) == 1
    stub.assert_exhausted()


# ---------------------------------------------------------------------------
# T027 - exit-code matrix
# ---------------------------------------------------------------------------


def test_unsatisfied_threshold_exits_two(tmp_path: Path, monkeypatch) -> None:
    config_path = _workspace(tmp_path, "thresholds:\n  accuracy: '>=5.5'\n")
    out = tmp_path / "out"
    _script(AzdStub(), items=_items((4.0, 5.0))).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 2, result.output


def test_declared_metric_never_emitted_exits_two(tmp_path: Path, monkeypatch) -> None:
    config_path = _workspace(tmp_path, "thresholds:\n  accuracy: '>=1'\n")
    out = tmp_path / "out"
    items = [
        {
            "id": "item-0",
            "run_id": "run-1",
            "status": "passed",
            "datasource_item": {"input": "q"},
            "results": [
                {"metric": "builtin.task_adherence", "score": 5, "passed": True}
            ],
        }
    ]
    _script(AzdStub(), items=items).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 2, result.output
    payload = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert payload["config"]["azd_evaluation"]["missing_metrics"] == ["accuracy"]


def test_unmatched_threshold_exits_one_without_invoking_azd(
    tmp_path: Path, monkeypatch
) -> None:
    """A typo must never consume a cloud evaluation."""

    config_path = _workspace(tmp_path, "thresholds:\n  not_a_metric: '>=3'\n")
    out = tmp_path / "out"
    stub = AzdStub().install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 1, result.output
    assert "not_a_metric" in result.output
    assert "Traceback" not in result.output
    stub.assert_never_invoked()


@pytest.mark.parametrize("status", ["failed", "error", "canceled", "cancelled"])
def test_terminal_failure_status_exits_one(
    tmp_path: Path, monkeypatch, status: str
) -> None:
    config_path = _workspace(tmp_path)
    out = tmp_path / "out"
    _script(AzdStub(), statuses=(status,)).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output


def test_missing_extension_exits_one_with_an_actionable_message(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _workspace(tmp_path)
    out = tmp_path / "out"
    stub = AzdStub()
    stub.expect("azd", "version")
    stub.expect("extension", "list", "--installed", stdout=extension_list_json("azure.ai.agents"))
    stub.install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code == 1, result.output
    assert CURRENT in result.output
    assert "Traceback" not in result.output


def test_zero_samples_does_not_report_a_pass(tmp_path: Path, monkeypatch) -> None:
    config_path = _workspace(tmp_path)
    out = tmp_path / "out"
    payload = _run_payload("completed", total=0, passed=0)
    _script(AzdStub(), items=[], run_payload=payload).install(monkeypatch)

    result = _invoke(config_path, out)

    assert result.exit_code != 0, result.output


# ---------------------------------------------------------------------------
# T028 - baseline comparison
# ---------------------------------------------------------------------------


def test_baseline_comparison_works_for_a_current_surface_run(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = _workspace(tmp_path, "thresholds:\n  accuracy: '>=4'\n")

    first = tmp_path / "first"
    _script(AzdStub(), items=_items((4.0, 5.0))).install(monkeypatch)
    assert _invoke(config_path, first).exit_code == 0

    second = tmp_path / "second"
    _script(AzdStub(), items=_items((4.0, 4.0))).install(monkeypatch)
    result = _invoke(config_path, second, "--baseline", str(first / "results.json"))

    assert result.exit_code == 0, result.output
    payload = json.loads((second / "results.json").read_text(encoding="utf-8"))
    assert payload["comparison"] is not None
    accuracy = [
        metric
        for metric in payload["comparison"]["metrics"]
        if metric["metric"] == "accuracy"
    ]
    assert accuracy and accuracy[0]["direction"] == "regressed"
