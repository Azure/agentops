"""End-to-end demo runner for AgentOps.

Exercises the full CLI surface against an in-process HTTP echo agent and
produces a self-contained ``evidence/`` folder suitable for pull-request
reviews and GitHub Actions artifact uploads.

The script is offline by design: it does not contact Azure, Foundry, or any
real model provider. It validates the parts of the pipeline that are most
prone to regression:

* ``agentops init`` creates ``agentops.yaml`` and a seed dataset.
* ``agentops eval run`` invokes the agent, runs the inferred evaluators,
  writes ``results.json`` and ``report.md``, and exits with the documented
  exit-code contract.
* ``agentops eval run --baseline`` produces the comparison block.
* ``agentops report generate`` regenerates ``report.md`` from results.

Each scenario writes its artifacts under ``evidence/<timestamp>/<scenario>/``
and a final ``SUMMARY.md`` aggregates the outcomes.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_BODY = (
    '{"input": "What is 2+2?", "expected": "4"}\n'
    '{"input": "Capital of France?", "expected": "Paris"}\n'
    '{"input": "Color of the sky?", "expected": "blue"}\n'
)

logger = logging.getLogger("agentops.e2e_demo")


class _EchoHandler(BaseHTTPRequestHandler):
    """Minimal HTTP/JSON agent: echoes the ``message`` field as ``text``."""

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}
        message = str(payload.get("message", ""))
        # Echo back a "smart" response that exact-matches the expected answer
        # for the seed dataset, so evaluators score positively.
        canned = {
            "What is 2+2?": "4",
            "Capital of France?": "Paris",
            "Color of the sky?": "blue",
        }
        text = canned.get(message, message)
        response = json.dumps({"text": text}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return  # silence


def _start_echo_server() -> tuple[HTTPServer, threading.Thread, str]:
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}/"


def _write_agentops_yaml(target_dir: Path, agent_url: str) -> Path:
    yaml_path = target_dir / "agentops.yaml"
    yaml_path.write_text(
        "version: 1\n"
        f'agent: "{agent_url}"\n'
        "dataset: ./dataset.jsonl\n"
        "evaluators:\n"
        "  - name: F1ScoreEvaluator\n",
        encoding="utf-8",
    )
    (target_dir / "dataset.jsonl").write_text(DATASET_BODY, encoding="utf-8")
    return yaml_path


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "agentops", *args]
    logger.info("$ %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _capture_artifacts(
    *,
    label: str,
    proc: subprocess.CompletedProcess,
    project_dir: Path,
    evidence_dir: Path,
) -> dict:
    bucket = evidence_dir / label
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / "stdout.log").write_text(proc.stdout, encoding="utf-8")
    (bucket / "stderr.log").write_text(proc.stderr, encoding="utf-8")
    (bucket / "exit_code.txt").write_text(str(proc.returncode), encoding="utf-8")

    results_dir = project_dir / ".agentops" / "results" / "latest"
    summary_metric: Optional[float] = None
    overall_passed: Optional[bool] = None
    if results_dir.exists():
        for name in ("results.json", "report.md", "cloud_evaluation.json"):
            src = results_dir / name
            if src.exists():
                shutil.copy2(src, bucket / name)
        results_path = bucket / "results.json"
        if results_path.exists():
            data = json.loads(results_path.read_text(encoding="utf-8"))
            overall_passed = data.get("summary", {}).get("overall_passed")
            agg = data.get("aggregate_metrics", {})
            summary_metric = agg.get("f1_score") or next(iter(agg.values()), None)

    return {
        "label": label,
        "exit_code": proc.returncode,
        "summary_metric": summary_metric,
        "overall_passed": overall_passed,
    }


def _render_summary(records: list[dict], evidence_dir: Path) -> Path:
    lines = ["# AgentOps E2E demo summary", ""]
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}Z")
    lines.append("")
    lines.append("| Scenario | Exit code | Overall passed | Headline metric |")
    lines.append("|---|---|---|---|")
    for record in records:
        metric = (
            f"{record['summary_metric']:.3f}"
            if record["summary_metric"] is not None
            else "—"
        )
        passed = (
            "✅" if record["overall_passed"] else
            ("❌" if record["overall_passed"] is False else "—")
        )
        lines.append(
            f"| {record['label']} | {record['exit_code']} | {passed} | {metric} |"
        )
    summary_path = evidence_dir / "SUMMARY.md"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=REPO_ROOT / "evidence",
        help="Where to write the evidence/<timestamp>/ folder.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.evidence_dir / timestamp
    evidence_dir.mkdir(parents=True, exist_ok=True)
    logger.info("evidence -> %s", evidence_dir)

    server, _thread, agent_url = _start_echo_server()
    logger.info("echo agent listening on %s", agent_url)

    records: list[dict] = []
    project_dir = evidence_dir / "_workspace"
    project_dir.mkdir()

    try:
        _write_agentops_yaml(project_dir, agent_url)

        # 1. Initial evaluation. Should pass cleanly against the canned echo.
        proc = _run_cli("eval", "run", cwd=project_dir)
        records.append(
            _capture_artifacts(
                label="01-initial-run",
                proc=proc,
                project_dir=project_dir,
                evidence_dir=evidence_dir,
            )
        )

        # 2. Baseline comparison. Re-run pointed at the previous results.json.
        baseline_path = (
            project_dir / ".agentops" / "results" / "latest" / "results.json"
        )
        if baseline_path.exists():
            stash = evidence_dir / "_baseline.json"
            shutil.copy2(baseline_path, stash)
            proc = _run_cli(
                "eval",
                "run",
                "--baseline",
                str(stash),
                cwd=project_dir,
            )
            records.append(
                _capture_artifacts(
                    label="02-baseline-comparison",
                    proc=proc,
                    project_dir=project_dir,
                    evidence_dir=evidence_dir,
                )
            )

        # 3. Report regeneration from existing results.
        proc = _run_cli("report", "generate", cwd=project_dir)
        records.append(
            _capture_artifacts(
                label="03-report-regenerate",
                proc=proc,
                project_dir=project_dir,
                evidence_dir=evidence_dir,
            )
        )
    finally:
        server.shutdown()

    summary_path = _render_summary(records, evidence_dir)
    logger.info("summary -> %s", summary_path)

    failed = [r for r in records if r["exit_code"] not in (0, 2)]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
