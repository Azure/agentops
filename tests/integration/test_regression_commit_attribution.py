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
import subprocess
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


class _ToggleHandler(BaseHTTPRequestHandler):
    """Answers correctly or incorrectly depending on a shared class-level
    flag, so the *same* agent URL (and dataset) can simulate "this agent's
    behavior changed between two commits" without actually changing
    identity - build_regression_insight now requires the compared runs'
    agent/dataset/evaluators to match (FR-005 comparability) before
    producing a causal insight, which a real `--baseline` comparison
    against a different dataset/endpoint correctly no longer does.
    """

    answer_correctly = True

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if type(self).answer_correctly:
            message = body.get("message", "")
            answer = _EXACT_ANSWERS.get(message, "")
        else:
            answer = "completely unrelated response"
        payload = json.dumps({"text": answer}).encode("utf-8")
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


@pytest.fixture()
def toggle_server():
    _ToggleHandler.answer_correctly = True
    server, thread, url = _serve(_ToggleHandler)
    try:
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=1)


def _run_baseline_then_regressed(tmp_path: Path, monkeypatch, agent_url: str):
    """Runs a good then a regressed evaluation against the *same* agent
    URL and dataset (only the toggle server's behavior differs between
    them), with commit capture mocked.

    Returns (baseline_result, current_result, current_dir).
    """
    dataset_path = tmp_path / "dataset.jsonl"
    _write_dataset(dataset_path)

    commits = iter([_fake_commit("a" * 40), _fake_commit("b" * 40)])
    monkeypatch.setattr(
        orchestrator, "resolve_commit_info", lambda **kwargs: next(commits)
    )

    baseline_config_path = tmp_path / "agentops-baseline.yaml"
    _write_config(baseline_config_path, agent_url=agent_url, dataset=dataset_path)
    baseline_config = load_agentops_config(baseline_config_path)

    _ToggleHandler.answer_correctly = True
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
    _write_config(current_config_path, agent_url=agent_url, dataset=dataset_path)
    current_config = load_agentops_config(current_config_path)

    _ToggleHandler.answer_correctly = False
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
    tmp_path: Path, monkeypatch, toggle_server
) -> None:
    baseline_result, current_result, current_dir = _run_baseline_then_regressed(
        tmp_path, monkeypatch, toggle_server
    )

    assert baseline_result.aggregate_metrics["f1_score"] == pytest.approx(1.0)
    assert baseline_result.commit is not None and baseline_result.commit.sha == "a" * 40

    assert current_result.aggregate_metrics["f1_score"] < baseline_result.aggregate_metrics["f1_score"]
    assert current_result.commit is not None and current_result.commit.sha == "b" * 40
    assert current_result.comparison is not None
    assert current_result.comparison.insight is not None

    insight = current_result.comparison.insight
    assert len(insight.regressed_metrics) == 1
    assert insight.regressed_metrics[0].metric == "f1_score"
    assert insight.regressed_metrics[0].from_value == pytest.approx(1.0)
    # Same agent/dataset/evaluators on both sides (FR-005 comparability) -
    # only the toggle server's behavior differs, which build_changed_inputs
    # has no way to see, so there is no tracked "changed input" here; the
    # explanation still names the metric's before/after values.
    assert insight.changed_inputs == []

    report_text = (current_dir / "report.md").read_text(encoding="utf-8")
    assert "## Regression Insight" in report_text
    assert insight.explanation in report_text

    # This feature is informational only - exit code is driven purely by
    # configured thresholds, unaffected by the presence of an insight.
    code = exit_code_from(current_result)
    assert code in (0, 2)


def test_regression_commit_attribution_purely_local(
    tmp_path: Path, monkeypatch, toggle_server
) -> None:
    """The same outcome holds with no CI env vars - commit resolved via local git."""
    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)

    _baseline_result, current_result, current_dir = _run_baseline_then_regressed(
        tmp_path, monkeypatch, toggle_server
    )

    # Commit capture itself is mocked here (as in the CI-style test above) to
    # keep the scenario deterministic; what this test additionally proves is
    # that the same regression-insight pipeline works with no CI environment
    # variables present, i.e. it is not CI-only in practice.
    assert current_result.comparison is not None
    assert current_result.comparison.insight is not None
    report_text = (current_dir / "report.md").read_text(encoding="utf-8")
    assert "## Regression Insight" in report_text


def _run_git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def test_commit_is_captured_before_execution_not_after(
    tmp_path: Path, monkeypatch, good_and_bad_servers
) -> None:
    """A local run's commit must reflect the repo's HEAD when the run
    *started*, not whatever HEAD happens to be once execution (the
    row-by-row agent/evaluator loop, which can take a while) finishes - a
    commit made to the repo mid-run must not retroactively change which
    commit this run is attributed to.
    """
    good_url, _bad_url = good_and_bad_servers
    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)

    repo = tmp_path
    _run_git(["init"], cwd=repo)
    _run_git(["config", "user.email", "dev@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Dev"], cwd=repo)
    (repo / "README.md").write_text("before\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=repo)
    _run_git(["commit", "-m", "Commit present when the run starts"], cwd=repo)
    sha_at_start = _run_git(["rev-parse", "HEAD"], cwd=repo)

    dataset_path = repo / "dataset.jsonl"
    _write_dataset(dataset_path)
    config_path = repo / "agentops.yaml"
    _write_config(config_path, agent_url=good_url, dataset=dataset_path)
    config = load_agentops_config(config_path)

    original_evaluate_row = orchestrator._evaluate_row

    def _evaluate_row_then_commit_mid_run(**kwargs):
        row_result = original_evaluate_row(**kwargs)
        if kwargs.get("index") == 0:
            # Simulate the developer committing/checking out something
            # else while this (slow, real-world) run is still executing.
            (repo / "README.md").write_text("after\n", encoding="utf-8")
            _run_git(["commit", "-am", "Made while the run was executing"], cwd=repo)
        return row_result

    monkeypatch.setattr(
        orchestrator, "_evaluate_row", _evaluate_row_then_commit_mid_run
    )

    result = run_evaluation(
        config,
        options=RunOptions(
            config_path=config_path,
            output_dir=tmp_path / "out",
            timeout_seconds=10.0,
        ),
    )

    sha_after_run = _run_git(["rev-parse", "HEAD"], cwd=repo)
    assert sha_after_run != sha_at_start  # the mid-run commit really happened
    assert result.commit is not None
    assert result.commit.sha == sha_at_start
    assert result.commit.sha != sha_after_run
