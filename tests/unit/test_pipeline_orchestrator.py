from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentops.core.agentops_config import AgentOpsConfig
from agentops.core.results import RowMetric, RunResult, RunSummary, TargetInfo
from agentops.pipeline import orchestrator
from agentops.pipeline.orchestrator import RunOptions
from agentops.services import dataset_source as dataset_source_service


def _run_git(args: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _init_repo(path: Path, *, commit_subject: str) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["config", "user.email", "dev@example.com"], cwd=path)
    _run_git(["config", "user.name", "Dev"], cwd=path)
    (path / "README.md").write_text(commit_subject, encoding="utf-8")
    _run_git(["add", "README.md"], cwd=path)
    _run_git(["commit", "-m", commit_subject], cwd=path)
    return _run_git(["rev-parse", "HEAD"], cwd=path)


def _minimal_run_result() -> RunResult:
    return RunResult(
        started_at="2026-10-07T00:00:00+00:00",
        finished_at="2026-10-07T00:00:01+00:00",
        duration_seconds=1.0,
        target=TargetInfo(kind="http", raw="https://example.test/chat"),
        dataset_path="dataset.jsonl",
        summary=RunSummary(
            items_total=0,
            items_passed_all=0,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
    )


def test_commit_resolved_from_config_workspace_not_process_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression test: git must run against the config's repo, not cwd.

    Reproduces the reported scenario of invoking ``agentops eval run
    --config /work/my-agent/agentops.yaml`` from an unrelated cwd
    (``/work/other-project``) - the recorded commit must be the config
    repo's HEAD, never the cwd repo's.
    """
    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)

    agent_repo = tmp_path / "my-agent"
    agent_sha = _init_repo(agent_repo, commit_subject="agent repo commit")
    other_repo = tmp_path / "other-project"
    other_sha = _init_repo(other_repo, commit_subject="unrelated repo commit")
    assert agent_sha != other_sha

    config_path = agent_repo / "agentops.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    options = RunOptions(config_path=config_path, output_dir=agent_repo / "out")

    original_cwd = Path.cwd()
    monkeypatch.chdir(other_repo)
    try:
        result = _minimal_run_result()
        orchestrator._finalize_commit_and_comparison(result, options)
    finally:
        monkeypatch.chdir(original_cwd)

    assert result.commit is not None
    assert result.commit.sha == agent_sha
    assert result.commit.sha != other_sha


def test_persist_resolves_commit_from_explicit_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    for env_var in ("GITHUB_SHA", "BUILD_SOURCEVERSION", "Build.SourceVersion"):
        monkeypatch.delenv(env_var, raising=False)

    agent_repo = tmp_path / "my-agent"
    agent_sha = _init_repo(agent_repo, commit_subject="agent repo commit")
    other_repo = tmp_path / "other-project"
    _init_repo(other_repo, commit_subject="unrelated repo commit")

    output_dir = agent_repo / "out"
    original_cwd = Path.cwd()
    monkeypatch.chdir(other_repo)
    try:
        result = _minimal_run_result()
        orchestrator._persist(result, output_dir, workspace=agent_repo)
    finally:
        monkeypatch.chdir(original_cwd)

    assert result.commit is not None
    assert result.commit.sha == agent_sha


def test_remote_dataset_is_resolved_once_and_provenance_survives_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_uri = (
        "https://examplestorage.blob.core.windows.net/evals/regression.jsonl"
    )
    materialized = tmp_path / "private-snapshot.jsonl"
    materialized.write_text(
        json.dumps(
            {
                "input": "hello",
                "expected": "hi",
                "response": "hi",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    state = {"resolutions": 0, "cleaned": False}

    @contextmanager
    def fake_resolve(value, **kwargs):
        state["resolutions"] += 1
        yield SimpleNamespace(
            local_path=materialized,
            provenance=source_uri,
            display_name="regression.jsonl",
            temporary=True,
        )
        materialized.unlink()
        state["cleaned"] = True

    span_arguments: dict[str, object] = {}

    @contextmanager
    def fake_span(**kwargs):
        span_arguments.update(kwargs)
        yield None

    monkeypatch.setattr(orchestrator, "resolve_dataset_source", fake_resolve)
    monkeypatch.setattr(orchestrator, "detect_dataset_shape", lambda _path: set())
    monkeypatch.setattr(orchestrator, "select_evaluators", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator.runtime, "load_evaluators", lambda _presets: {})
    monkeypatch.setattr(orchestrator.telemetry, "eval_run_span", fake_span)
    monkeypatch.setattr(orchestrator.telemetry, "init_tracing", lambda: None)
    monkeypatch.setattr(orchestrator.telemetry, "shutdown", lambda: None)

    progress: list[str] = []
    result = orchestrator.run_evaluation(
        AgentOpsConfig(
            version=1,
            agent="https://example.test/chat",
            dataset=source_uri,
            response_source="dataset",
        ),
        options=orchestrator.RunOptions(
            config_path=tmp_path / "agentops.yaml",
            output_dir=tmp_path / "out",
            progress=progress.append,
        ),
    )

    assert state == {"resolutions": 1, "cleaned": True}
    assert result.dataset_path == source_uri
    assert result.rows[0].response == "hi"
    assert span_arguments["dataset_name"] == source_uri
    assert source_uri in "\n".join(progress)
    assert "private-snapshot" not in "\n".join(progress)
    assert materialized.as_posix() not in (tmp_path / "out" / "results.json").read_text(
        encoding="utf-8"
    )


def test_azd_execution_does_not_resolve_agentops_dataset(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = AgentOpsConfig(
        version=1,
        agent="support-agent:1",
        dataset="https://examplestorage.blob.core.windows.net/evals/data.jsonl",
        execution="azd",
        eval_recipe=Path("eval.yaml"),
    )
    monkeypatch.setattr(
        orchestrator,
        "resolve_dataset_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("azd must keep recipe-owned dataset behavior")
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "_run_evaluation_azd",
        lambda config, *, options: "azd-result",
    )

    assert (
        orchestrator._run_evaluation(
            config,
            options=orchestrator.RunOptions(
                config_path=tmp_path / "agentops.yaml",
                output_dir=tmp_path / "out",
            ),
        )
        == "azd-result"
    )


class _ParityDatasetDownloader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def chunks(self):
        midpoint = len(self._payload) // 2
        yield self._payload[:midpoint]
        yield self._payload[midpoint:]


class _ParityDatasetClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def get_properties(self):
        return SimpleNamespace(size=len(self._payload), etag=None, last_modified=None)

    def download(self, **_kwargs):
        return _ParityDatasetDownloader(self._payload)


@pytest.mark.parametrize(
    ("source_kind", "dataset_value"),
    [
        ("local", "parity.jsonl"),
        (
            "blob",
            "https://account.blob.core.windows.net/evaluations/parity.jsonl",
        ),
        (
            "adls",
            "https://account.dfs.core.windows.net/evaluations/parity.jsonl",
        ),
    ],
    ids=["local", "blob", "adls"],
)
def test_run_evaluation_preserves_dataset_semantics_across_sources(
    tmp_path: Path,
    monkeypatch,
    source_kind: str,
    dataset_value: str,
) -> None:
    payload = (
        b'{"input":"first","expected":"alpha","response":"alpha"}\n'
        b'{"input":"second","expected":"beta","response":"beta"}\n'
    )
    local_dataset = tmp_path / "parity.jsonl"
    local_dataset.write_bytes(payload)

    if source_kind != "local":
        client = _ParityDatasetClient(payload)
        monkeypatch.setattr(
            dataset_source_service,
            "_create_storage_client",
            lambda _reference: client,
        )

    monkeypatch.setattr(
        orchestrator.runtime,
        "load_evaluators",
        lambda presets: [SimpleNamespace(preset=preset) for preset in presets],
    )
    monkeypatch.setattr(
        orchestrator.runtime,
        "run_evaluator",
        lambda evaluator, **_kwargs: RowMetric(
            name=evaluator.preset.score_key,
            value=(
                0.01
                if evaluator.preset.score_key == "avg_latency_seconds"
                else 5.0
            ),
        ),
    )
    monkeypatch.setattr(orchestrator.telemetry, "init_tracing", lambda: None)
    monkeypatch.setattr(orchestrator.telemetry, "shutdown", lambda: None)

    result = orchestrator.run_evaluation(
        AgentOpsConfig(
            version=1,
            agent="https://example.test/chat",
            dataset=dataset_value,
            protocol="http-json",
            response_source="dataset",
        ),
        options=orchestrator.RunOptions(
            config_path=tmp_path / "agentops.yaml",
            output_dir=tmp_path / "out",
        ),
    )

    expected_evaluators = [
        "CoherenceEvaluator",
        "FluencyEvaluator",
        "SimilarityEvaluator",
        "ResponseCompletenessEvaluator",
        "avg_latency_seconds",
    ]
    expected_metrics = {
        "coherence": 5.0,
        "fluency": 5.0,
        "similarity": 5.0,
        "response_completeness": 5.0,
        "avg_latency_seconds": 0.01,
    }

    assert result.dataset_path == (
        str(local_dataset.resolve()) if source_kind == "local" else dataset_value
    )
    assert result.evaluators == expected_evaluators
    assert [
        (
            row.row_index,
            row.input,
            row.expected,
            row.response,
            row.error,
            {metric.name: metric.value for metric in row.metrics},
        )
        for row in result.rows
    ] == [
        (0, "first", "alpha", "alpha", None, expected_metrics),
        (1, "second", "beta", "beta", None, expected_metrics),
    ]
    assert result.aggregate_metrics == expected_metrics
    assert all(threshold.passed for threshold in result.thresholds)
    assert result.summary.model_dump() == {
        "items_total": 2,
        "items_passed_all": 2,
        "items_pass_rate": 1.0,
        "thresholds_total": 5,
        "thresholds_passed": 5,
        "threshold_pass_rate": 1.0,
        "overall_passed": True,
    }
    assert not list((tmp_path / ".agentops" / ".resolved").glob("*.jsonl"))


def test_persist_skips_second_commit_resolution_when_already_attempted(
    tmp_path: Path, monkeypatch
) -> None:
    """When ``_finalize_commit_and_comparison`` already tried (and, as here,
    failed) to resolve the commit, ``_persist`` must not attempt it again -
    each attempt runs real `git` subprocesses with their own timeout, so a
    second identical attempt would only double the cost for the same
    (failed) outcome."""
    calls = {"count": 0}

    def _fake_resolve_commit_info(*, workspace):
        calls["count"] += 1
        return None

    monkeypatch.setattr(
        orchestrator, "resolve_commit_info", _fake_resolve_commit_info
    )

    config_path = tmp_path / "agentops.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    options = RunOptions(config_path=config_path, output_dir=tmp_path / "out")

    result = _minimal_run_result()
    orchestrator._finalize_commit_and_comparison(result, options)
    assert calls["count"] == 1
    assert result.commit is None

    orchestrator._persist(
        result,
        options.output_dir,
        workspace=options.config_path.parent,
        commit_resolution_attempted=True,
    )

    assert calls["count"] == 1
    assert result.commit is None


def test_persist_still_attempts_commit_resolution_when_called_standalone(
    tmp_path: Path, monkeypatch
) -> None:
    """A caller that skips ``_finalize_commit_and_comparison`` (unlike every
    current orchestrator call site) still gets the fallback, since
    ``commit_resolution_attempted`` defaults to ``False``."""
    calls = {"count": 0}

    def _fake_resolve_commit_info(*, workspace):
        calls["count"] += 1
        return None

    monkeypatch.setattr(
        orchestrator, "resolve_commit_info", _fake_resolve_commit_info
    )

    result = _minimal_run_result()
    orchestrator._persist(result, tmp_path / "out", workspace=tmp_path)

    assert calls["count"] == 1
