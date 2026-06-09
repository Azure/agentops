"""Orchestrate the open-source ASSERT (assert-ai) CLI from AgentOps.

This service wraps the `responsibleai/ASSERT` framework so AgentOps can
actively *run* ASSERT (not just reference pre-generated artifacts via
``assert_path``). The flow is:

1. Validate that the ``assert-ai`` CLI is installed and reachable.
2. Invoke ``assert-ai run --config <eval_config.yaml>`` as a subprocess.
3. Discover the run's output directory under ``<results_dir>/<suite>/<run>/``.
4. Read ``metrics.json`` and ``scores.jsonl`` to produce a normalized summary.
5. Write a stable normalized JSON the evidence pack can consume.

AgentOps does NOT reimplement ASSERT. The orchestration boundary is the CLI:
all spec systematization, test-set generation, inference, and LLM-judging
remain in ASSERT itself. AgentOps only manages invocation and collects the
artifacts ASSERT writes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

NORMALIZED_RESULT_FILENAME = "latest.json"
DEFAULT_NORMALIZED_DIR = Path(".agentops") / "assert"
DEFAULT_RESULTS_DIR = Path("artifacts") / "results"


class AssertRunnerError(RuntimeError):
    """Raised when ASSERT cannot be invoked or its output cannot be read."""


@dataclass(frozen=True)
class AssertRunResult:
    """Normalized summary of a single ASSERT run."""

    suite: str
    run_id: str
    config_path: str
    results_dir: str
    run_output_dir: str
    metrics: dict[str, Any] = field(default_factory=dict)
    dimension_summary: dict[str, dict[str, Any]] = field(default_factory=dict)
    total_cases: int = 0
    failed_cases: int = 0
    passed_cases: int = 0
    pass_rate: Optional[float] = None
    has_violations: bool = False
    exit_code: int = 0
    normalized_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_assert_installed(executable: str = "assert-ai") -> bool:
    """Return ``True`` when the ``assert-ai`` CLI is on ``PATH``."""

    return shutil.which(executable) is not None


def assert_version(executable: str = "assert-ai") -> Optional[str]:
    """Best-effort lookup of the installed ASSERT CLI version string."""

    if not is_assert_installed(executable):
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    return output or None


def run_assert(
    *,
    workspace: Path,
    config_path: Path,
    results_dir: Optional[Path] = None,
    suite: Optional[str] = None,
    run_id: Optional[str] = None,
    extra_args: Optional[Iterable[str]] = None,
    executable: str = "assert-ai",
    env: Optional[dict[str, str]] = None,
    stream_output: bool = True,
    normalized_output: Optional[Path] = None,
) -> AssertRunResult:
    """Invoke ``assert-ai run`` and return a normalized summary.

    The function does not raise on ASSERT failure exit codes; callers decide
    whether to fail the pipeline based on ``has_violations`` and
    ``exit_code``. It does raise :class:`AssertRunnerError` when the CLI is
    missing, the config path is invalid, or ASSERT's output cannot be parsed.
    """

    if not config_path.exists():
        raise AssertRunnerError(
            f"ASSERT config file does not exist: {config_path}"
        )
    if not is_assert_installed(executable):
        raise AssertRunnerError(
            "The 'assert-ai' CLI is not installed. Install it with "
            "'pip install assert-ai' (see https://github.com/responsibleai/ASSERT)."
        )

    inferred_suite, inferred_run_id = _read_suite_and_run_from_config(config_path)
    suite = suite or inferred_suite
    run_id = run_id or inferred_run_id

    resolved_results_dir = (results_dir or DEFAULT_RESULTS_DIR).resolve()
    resolved_results_dir.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [executable, "run", "--config", str(config_path)]
    if extra_args:
        cmd.extend(extra_args)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    completed = subprocess.run(
        cmd,
        cwd=str(workspace),
        env=run_env,
        text=True,
        capture_output=not stream_output,
        check=False,
    )

    run_output_dir = _locate_run_output(
        results_dir=resolved_results_dir,
        suite=suite,
        run_id=run_id,
    )
    if run_output_dir is None:
        raise AssertRunnerError(
            "ASSERT finished but no run output directory was found under "
            f"{resolved_results_dir}. Confirm 'suite' and 'run_id' in your "
            "eval_config.yaml or pass --suite/--run-id."
        )

    metrics = _read_metrics(run_output_dir)
    dimension_summary = _summarize_dimensions(run_output_dir)
    totals = _aggregate_totals(metrics, dimension_summary)

    normalized_target = (
        normalized_output
        if normalized_output is not None
        else workspace / DEFAULT_NORMALIZED_DIR / NORMALIZED_RESULT_FILENAME
    )
    normalized_target.parent.mkdir(parents=True, exist_ok=True)

    result = AssertRunResult(
        suite=str(suite or run_output_dir.parent.name),
        run_id=str(run_id or run_output_dir.name),
        config_path=str(config_path),
        results_dir=str(resolved_results_dir),
        run_output_dir=str(run_output_dir),
        metrics=metrics,
        dimension_summary=dimension_summary,
        total_cases=totals["total"],
        failed_cases=totals["failed"],
        passed_cases=totals["passed"],
        pass_rate=totals["pass_rate"],
        has_violations=totals["failed"] > 0,
        exit_code=completed.returncode,
        normalized_path=str(normalized_target),
    )

    normalized_target.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result


def _read_suite_and_run_from_config(config_path: Path) -> tuple[Optional[str], Optional[str]]:
    try:
        yaml = YAML(typ="safe")
        data = yaml.load(config_path.read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    suite = (
        data.get("suite_id")
        or data.get("suite")
        or (data.get("evaluation") or {}).get("suite_id")
        if isinstance(data.get("evaluation"), dict)
        else data.get("suite_id") or data.get("suite")
    )
    run_id = (
        data.get("run_id")
        or data.get("run")
        or (data.get("evaluation") or {}).get("run_id")
        if isinstance(data.get("evaluation"), dict)
        else data.get("run_id") or data.get("run")
    )
    return (
        str(suite) if isinstance(suite, (str, int)) else None,
        str(run_id) if isinstance(run_id, (str, int)) else None,
    )


def _locate_run_output(
    *,
    results_dir: Path,
    suite: Optional[str],
    run_id: Optional[str],
) -> Optional[Path]:
    if suite and run_id:
        candidate = results_dir / suite / run_id
        if candidate.is_dir():
            return candidate
    if suite:
        suite_dir = results_dir / suite
        if suite_dir.is_dir():
            runs = sorted(
                (p for p in suite_dir.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if runs:
                return runs[0]
    if results_dir.is_dir():
        suites = sorted(
            (p for p in results_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for suite_dir in suites:
            runs = sorted(
                (p for p in suite_dir.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if runs:
                return runs[0]
    return None


def _read_metrics(run_dir: Path) -> dict[str, Any]:
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.is_file():
        return {}
    try:
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertRunnerError(
            f"Could not parse ASSERT metrics.json at {metrics_path}: {exc}"
        ) from exc


def _summarize_dimensions(run_dir: Path) -> dict[str, dict[str, Any]]:
    scores_path = run_dir / "scores.jsonl"
    if not scores_path.is_file():
        return {}
    summary: dict[str, dict[str, Any]] = {}
    try:
        with scores_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                dimension = record.get("dimension") or record.get("metric")
                if not dimension:
                    continue
                verdict = (record.get("verdict") or record.get("status") or "").lower()
                bucket = summary.setdefault(
                    str(dimension),
                    {"total": 0, "violations": 0, "passes": 0, "other": 0},
                )
                bucket["total"] += 1
                if verdict in {"violation", "fail", "failed", "violated"}:
                    bucket["violations"] += 1
                elif verdict in {"pass", "passed", "ok", "satisfied"}:
                    bucket["passes"] += 1
                else:
                    bucket["other"] += 1
    except OSError as exc:
        raise AssertRunnerError(
            f"Could not read ASSERT scores.jsonl at {scores_path}: {exc}"
        ) from exc
    return summary


def _aggregate_totals(
    metrics: dict[str, Any],
    dimensions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    total = 0
    failed = 0
    if isinstance(metrics, dict):
        totals_value = metrics.get("totals")
        candidates: dict[str, Any] = totals_value if isinstance(totals_value, dict) else metrics
        for key in ("total", "total_cases", "cases", "count"):
            if isinstance(candidates.get(key), int):
                total = candidates[key]
                break
        for key in ("violations", "failed", "failures", "fail_count"):
            if isinstance(candidates.get(key), int):
                failed = candidates[key]
                break
    if total == 0 and dimensions:
        total = max((bucket["total"] for bucket in dimensions.values()), default=0)
    if failed == 0 and dimensions:
        failed = sum(bucket["violations"] for bucket in dimensions.values())
    passed = max(total - failed, 0) if total else 0
    pass_rate = round(passed / total, 4) if total else None
    return {
        "total": int(total),
        "failed": int(failed),
        "passed": int(passed),
        "pass_rate": pass_rate,
    }
