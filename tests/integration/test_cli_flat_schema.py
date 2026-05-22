"""CLI tests for the 1.0 flat schema path on ``agentops eval run``."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip(
    "azure.ai.evaluation",
    reason="azure-ai-evaluation is required to instantiate evaluators in the pipeline runtime",
)

from agentops.cli.app import app

runner = CliRunner()


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


def _write_flat_config(path: Path, *, agent: str, dataset: Path) -> None:
    payload = {
        "version": 1,
        "agent": agent,
        "dataset": str(dataset),
        "evaluators": [{"name": "F1ScoreEvaluator"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_eval_run_routes_flat_schema_to_pipeline(
    tmp_path: Path, echo_server: str
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config = tmp_path / "agentops.yaml"
    _write_flat_config(config, agent=echo_server, dataset=dataset)
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--config",
            str(config),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code in (0, 2), result.output
    assert (output / "results.json").exists()
    assert (output / "report.md").exists()


def test_eval_run_supports_baseline_flag(tmp_path: Path, echo_server: str) -> None:
    dataset = tmp_path / "dataset.jsonl"
    _write_dataset(dataset)
    config = tmp_path / "agentops.yaml"
    _write_flat_config(config, agent=echo_server, dataset=dataset)

    baseline_dir = tmp_path / "baseline"
    runner.invoke(
        app,
        ["eval", "run", "--config", str(config), "--output", str(baseline_dir)],
    )
    current = tmp_path / "current"
    result = runner.invoke(
        app,
        [
            "eval",
            "run",
            "--config",
            str(config),
            "--output",
            str(current),
            "--baseline",
            str(baseline_dir / "results.json"),
        ],
    )
    assert result.exit_code in (0, 2), result.output
    payload = json.loads((current / "results.json").read_text(encoding="utf-8"))
    assert payload["comparison"] is not None
    assert payload["comparison"]["baseline_path"].endswith("results.json")
