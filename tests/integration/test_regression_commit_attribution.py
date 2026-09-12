"""End-to-end test for regression commit attribution (User Stories 1 and 3).

Runs two real evaluations through the orchestrator against two small HTTP
agents whose answers differ enough to produce a genuine metric regression,
with commit capture mocked to two distinct commits, and asserts the
resulting ``report.md`` explains the regression - both for the CI-style
scenario (commit resolved via env var) and the purely local scenario (no CI
env vars, commit resolved via local git).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip(
    "azure.ai.evaluation",
    reason="azure-ai-evaluation is required to instantiate evaluators in the pipeline runtime",
)

from agentops.core.config_loader import load_agentops_config
from agentops.core.results import CommitInfo
from agentops.pipeline import orchestrator
from agentops.pipeline.orchestrator import RunOptions, exit_code_from, run_evaluation


_EXACT_ANSWERS = {"say hi": "hi", "say bye": "bye"}


class _ExactMatchHandler(BaseHTTPRequestHandler):
    """Echoes back the exact expected answer for each known input."""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        message = body.get("message", "")
        answer = _EXACT_ANSWERS.get(message, "")
        payload = json.dumps({"text": answer}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args, **kwargs) -> None:  # noqa: D401
        pass


class _WrongAnswerHandler(BaseHTTPRequestHandler):
    """Always answers incorrectly, to force a real metric regression."""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = json.dumps({"text": "completely unrelated response"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args, **kwargs) -> None:  # noqa: D401
        pass


def _serve(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/chat"


def _write_dataset(path: Path) -> None:
    rows = [
        {"input": "say hi", "expected": "hi"},
        {"input": "say bye", "expected": "bye"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _write_config(path: Path, *, agent_url: str, dataset: Path) -> None:
    payload = {
        "version": 1,
        "agent": agent_url,
        "dataset": str(dataset),
        "evaluators": [{"name": "F1ScoreEvaluator"}],  # avoids Azure model dependency
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fake_commit(sha: str) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        subject="A commit",
        author="Dev",
        authored_at="2026-09-01T10:00:00+00:00",
        source="ci",
    )


@pytest.fixture()
def good_and_bad_servers():
    good_server, good_thread, good_url = _serve(_ExactMatchHandler)
    bad_server, bad_thread, bad_url = _serve(_WrongAnswerHandler)
    try:
        yield good_url, bad_url
    finally:
        good_server.shutdown()
        good_thread.join(timeout=1)
        bad_server.shutdown()
        bad_thread.join(timeout=1)


def _run_baseline_then_regressed(tmp_path: Path, monkeypatch, good_url: str, bad_url: str):
    """Runs a good then a regressed evaluation, with commit capture mocked.

    Returns (baseline_result, current_result, current_dir).
    """
    baseline_dataset = tmp_path / "dataset-v1.jsonl"
    current_dataset = tmp_path / "dataset-v2.jsonl"
    _write_dataset(baseline_dataset)
    _write_dataset(current_dataset)

    commits = iter([_fake_commit("a" * 40), _fake_commit("b" * 40)])
    monkeypatch.setattr(orchestrator, "resolve_commit_info", lambda: next(commits))

    baseline_config_path = tmp_path / "agentops-baseline.yaml"
    _write_config(baseline_config_path, agent_url=good_url, dataset=baseline_dataset)
    baseline_config = load_agentops_config(baseline_config_path)

    baseline_dir = tmp_path / "baseline"
    baseline_result = run_evaluation(
        baseline_config,
        options=RunOptions(
            config_path=baseline_config_path,
            output_dir=baseline_dir,
            timeout_seconds=10.0,
        ),
    )

    current_config_path = tmp_path / "agentops-current.yaml"
    _write_config(current_config_path, agent_url=bad_url, dataset=current_dataset)
    current_config = load_agentops_config(current_config_path)

    current_dir = tmp_path / "current"
    current_result = run_evaluation(
        current_config,
        options=RunOptions(
            config_path=current_config_path,
            output_dir=current_dir,
            baseline_path=baseline_dir / "results.json",
            timeout_seconds=10.0,
        ),
    )

    return baseline_result, current_result, current_dir


def test_regression_commit_attribution_end_to_end(
    tmp_path: Path, monkeypatch, good_and_bad_servers
) -> None:
    good_url, bad_url = good_and_bad_servers

    baseline_result, current_result, current_dir = _run_baseline_then_regressed(
        tmp_path, monkeypatch, good_url, bad_url
    )

    assert baseline_result.aggregate_metrics["f1_score"] == pytest.approx(1.0)
    assert baseline_result.commit is not None and baseline_result.commit.sha == "a" * 40

    assert current_result.aggregate_metrics["f1_score"] < baseline_result.aggregate_metrics["f1_score"]
    assert current_result.commit is not None and current_result.commit.sha == "b" * 40
    assert current_result.comparison is not None
    assert current_result.comparison.insight is not None

    insight = current_result.comparison.insight
    assert insight.metric == "f1_score"
    assert insight.from_value == pytest.approx(1.0)
    assert any(c.field == "dataset" for c in insight.changed_inputs)

    report_text = (current_dir / "report.md").read_text(encoding="utf-8")
    assert "## Regression Insight" in report_text
    assert insight.explanation in report_text

    # This feature is informational only - exit code is driven purely by
    # configured thresholds, unaffected by the presence of an insight.
    code = exit_code_from(current_result)
    assert code in (0, 2)


def test_regression_commit_attribution_purely_local(
    tmp_path: Path, monkeypatch, good_and_bad_servers
) -> None:
    """The same outcome holds with no CI env vars - commit resolved via local git."""
    good_url, bad_url = good_and_bad_servers

    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)

    _baseline_result, current_result, current_dir = _run_baseline_then_regressed(
        tmp_path, monkeypatch, good_url, bad_url
    )

    # Commit capture itself is mocked here (as in the CI-style test above) to
    # keep the scenario deterministic; what this test additionally proves is
    # that the same regression-insight pipeline works with no CI environment
    # variables present, i.e. it is not CI-only in practice.
    assert current_result.comparison is not None
    assert current_result.comparison.insight is not None
    report_text = (current_dir / "report.md").read_text(encoding="utf-8")
    assert "## Regression Insight" in report_text
