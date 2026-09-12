"""End-to-end smoke test for the AgentOps 1.0 pipeline.

Spins up a tiny HTTP server, points an ``agentops.yaml`` at it, runs the
orchestrator without any Azure dependencies (no AI-assisted evaluators), and
asserts the resulting ``results.json`` and ``report.md``.
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

from agentops.core.agentops_config import AgentOpsConfig
from agentops.core.config_loader import load_agentops_config
from agentops.pipeline.orchestrator import (
    RunOptions,
    exit_code_from,
    run_evaluation,
)


class _EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        message = body.get("message", "")
        payload = json.dumps({"text": f"echo: {message}"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args, **kwargs) -> None:  # noqa: D401
        pass


@pytest.fixture()
def echo_server():
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/chat"
    finally:
        server.shutdown()
        thread.join(timeout=1)


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


def test_http_pipeline_end_to_end(tmp_path: Path, echo_server: str) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)

    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path, agent_url=echo_server, dataset=dataset)

    config = load_agentops_config(config_path)
    assert isinstance(config, AgentOpsConfig)
    assert config.agent == echo_server

    output_dir = tmp_path / "results"
    options = RunOptions(
        config_path=config_path,
        output_dir=output_dir,
        timeout_seconds=10.0,
    )

    result = run_evaluation(config, options=options)

    assert (output_dir / "results.json").exists()
    assert (output_dir / "report.md").exists()
    assert result.summary.items_total == 2
    assert result.target.kind == "http_json"
    assert "f1_score" in result.aggregate_metrics
    assert result.rows[0].response.startswith("echo:")

    payload = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["target"]["url"] == echo_server

    code = exit_code_from(result)
    assert code in (0, 2)


def test_http_pipeline_with_baseline(tmp_path: Path, echo_server: str) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path, agent_url=echo_server, dataset=dataset)
    config = load_agentops_config(config_path)

    baseline_dir = tmp_path / "baseline"
    run_evaluation(
        config,
        options=RunOptions(
            config_path=config_path,
            output_dir=baseline_dir,
            timeout_seconds=10.0,
        ),
    )

    current_dir = tmp_path / "current"
    result = run_evaluation(
        config,
        options=RunOptions(
            config_path=config_path,
            output_dir=current_dir,
            baseline_path=baseline_dir / "results.json",
            timeout_seconds=10.0,
        ),
    )

    assert result.comparison is not None
    assert any(metric.metric == "f1_score" for metric in result.comparison.metrics)
    report_text = (current_dir / "report.md").read_text(encoding="utf-8")
    assert "Comparison vs Baseline" in report_text


def _clear_ci_sha_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)


def _git(args: list[str], *, cwd: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_local_run_captures_commit_metadata_inside_git_repo(
    tmp_path: Path, echo_server: str, monkeypatch
) -> None:
    """User Story 3: a local run in a git repo gets best-effort commit capture."""
    _clear_ci_sha_env_vars(monkeypatch)

    _git(["init"], cwd=tmp_path)
    _git(["config", "user.email", "dev@example.com"], cwd=tmp_path)
    _git(["config", "user.name", "Dev"], cwd=tmp_path)
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path, agent_url=echo_server, dataset=dataset)
    _git(["add", "-A"], cwd=tmp_path)
    _git(["commit", "-m", "Add eval config"], cwd=tmp_path)
    expected_sha = _git(["rev-parse", "HEAD"], cwd=tmp_path)

    monkeypatch.chdir(tmp_path)
    config = load_agentops_config(config_path)
    result = run_evaluation(
        config,
        options=RunOptions(
            config_path=config_path,
            output_dir=tmp_path / "results",
            timeout_seconds=10.0,
        ),
    )

    assert result.commit is not None
    assert result.commit.source == "local"
    assert result.commit.sha == expected_sha


def test_local_run_has_no_commit_metadata_outside_git_repo(
    tmp_path: Path, echo_server: str, monkeypatch
) -> None:
    """User Story 3: outside a git repo, the run still completes normally."""
    _clear_ci_sha_env_vars(monkeypatch)

    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config_path = tmp_path / "agentops.yaml"
    _write_config(config_path, agent_url=echo_server, dataset=dataset)

    monkeypatch.chdir(tmp_path)  # tmp_path is not a git repository
    config = load_agentops_config(config_path)
    output_dir = tmp_path / "results"
    result = run_evaluation(
        config,
        options=RunOptions(
            config_path=config_path,
            output_dir=output_dir,
            timeout_seconds=10.0,
        ),
    )

    assert result.commit is None
    assert (output_dir / "results.json").exists()
    assert (output_dir / "report.md").exists()
    code = exit_code_from(result)
    assert code in (0, 2)
