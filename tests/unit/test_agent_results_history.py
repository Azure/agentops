"""Tests for the results-history source."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from agentops.agent.config import FoundryControlSourceConfig, ResultsHistorySourceConfig
from agentops.agent.sources.results_history import collect_results_history


def _write_run(
    results_root: Path,
    run_id: str,
    timestamp: str,
    metrics: dict,
    *,
    items_total: int = 3,
    items_passed_all: int = 3,
    run_pass: bool = True,
) -> None:
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "metrics": metrics,
        "summary": {
            "run_pass": run_pass,
            "items_total": items_total,
            "items_passed_all": items_passed_all,
        },
    }
    (run_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")


def test_collect_results_history_orders_oldest_to_newest(tmp_path: Path) -> None:
    workspace = tmp_path
    results = workspace / ".agentops" / "results"
    _write_run(results, "run-1", "2024-05-01T10:00:00Z", {"coherence": 4.5})
    _write_run(results, "run-2", "2024-05-02T10:00:00Z", {"coherence": 4.0})
    _write_run(results, "latest", "2024-06-01T10:00:00Z", {"coherence": 1.0})

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert [r.run_id for r in history.runs] == ["run-1", "run-2"]
    assert history.runs[-1].metrics["coherence"] == 4.0
    assert history.diagnostics["status"] == "ok"


def test_collect_results_history_handles_missing_dir(tmp_path: Path) -> None:
    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(tmp_path, config)
    assert history.runs == []
    assert history.diagnostics["status"] == "missing"


def test_collect_results_history_disabled(tmp_path: Path) -> None:
    config = ResultsHistorySourceConfig(enabled=False)
    history = collect_results_history(tmp_path, config)
    assert history.runs == []
    assert history.diagnostics["status"] == "disabled"


def test_collect_results_history_loads_latest_only_in_ci(tmp_path: Path) -> None:
    """CI writes directly to .agentops/results/latest/ without a sibling dir.

    The loader must include `latest/` when no other timestamped dirs exist so
    the regression check sees the just-completed run as `latest` instead of
    falling back to a stale Foundry-listing entry.
    """
    workspace = tmp_path
    results = workspace / ".agentops" / "results"
    latest_dir = results / "latest"
    latest_dir.mkdir(parents=True)
    payload = {
        "version": 1,
        "started_at": "2026-05-31T12:54:00+00:00",
        "finished_at": "2026-05-31T12:54:04+00:00",
        "aggregate_metrics": {
            "coherence": 5.0,
            "similarity": 5.0,
        },
        "summary": {
            "overall_passed": True,
            "items_total": 3,
            "items_passed_all": 3,
        },
    }
    (latest_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert len(history.runs) == 1
    run = history.runs[0]
    assert run.run_id == "latest"
    assert run.metrics == {"coherence": 5.0, "similarity": 5.0}
    assert run.run_pass is True
    assert run.items_total == 3
    assert run.timestamp is not None
    assert run.timestamp.year == 2026


def test_collect_results_history_prefers_timestamped_over_latest(tmp_path: Path) -> None:
    """In dev mode both `latest/` and a timestamped dir exist (same run).

    The loader must skip `latest/` so the regression check does not see the
    same run under two different keys.
    """
    workspace = tmp_path
    results = workspace / ".agentops" / "results"
    # Timestamped dir wins; `latest/` is its sibling pointer.
    _write_run(results, "2026-05-31-12-54", "2026-05-31T12:54:00Z", {"coherence": 5.0})
    _write_run(results, "latest", "2026-05-31T12:54:00Z", {"coherence": 5.0})

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert [r.run_id for r in history.runs] == ["2026-05-31-12-54"]


def test_collect_results_history_reads_aggregate_metrics_field(tmp_path: Path) -> None:
    """The orchestrator writes `aggregate_metrics`, not `metrics`/`run_metrics`."""
    workspace = tmp_path
    results = workspace / ".agentops" / "results"
    run_dir = results / "run-1"
    run_dir.mkdir(parents=True)
    payload = {
        "version": 1,
        "started_at": "2026-05-30T10:00:00+00:00",
        "finished_at": "2026-05-30T10:00:30+00:00",
        "aggregate_metrics": {"coherence": 4.5, "fluency": 4.0},
        "summary": {
            "overall_passed": True,
            "items_total": 2,
            "items_passed_all": 2,
        },
    }
    (run_dir / "results.json").write_text(json.dumps(payload), encoding="utf-8")

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert len(history.runs) == 1
    assert history.runs[0].metrics == {"coherence": 4.5, "fluency": 4.0}
    assert history.runs[0].run_pass is True


def test_collect_results_history_orders_by_finished_at(tmp_path: Path) -> None:
    """`finished_at` should be preferred over `started_at` for ordering."""
    workspace = tmp_path
    results = workspace / ".agentops" / "results"

    # run-a started later but finished earlier (e.g., shorter run).
    run_a = results / "run-a"
    run_a.mkdir(parents=True)
    (run_a / "results.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-30T10:05:00+00:00",
                "finished_at": "2026-05-30T10:05:10+00:00",
                "aggregate_metrics": {"coherence": 4.0},
                "summary": {
                    "overall_passed": True,
                    "items_total": 1,
                    "items_passed_all": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    # run-b started earlier but finished later (long-running).
    run_b = results / "run-b"
    run_b.mkdir(parents=True)
    (run_b / "results.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-30T10:00:00+00:00",
                "finished_at": "2026-05-30T11:00:00+00:00",
                "aggregate_metrics": {"coherence": 3.0},
                "summary": {
                    "overall_passed": True,
                    "items_total": 1,
                    "items_passed_all": 1,
                },
            }
        ),
        encoding="utf-8",
    )

    config = ResultsHistorySourceConfig(
        enabled=True, path=".agentops/results", lookback_runs=10
    )
    history = collect_results_history(workspace, config)

    assert [r.run_id for r in history.runs] == ["run-a", "run-b"]


def test_collect_results_history_falls_back_to_foundry_cloud(
    tmp_path: Path, monkeypatch
) -> None:
    fake_openai = _fake_openai_client(
        runs=[
            SimpleNamespace(
                id="cloud-run-1",
                created_at="2024-05-01T10:00:00Z",
                status="succeeded",
                report_url="https://ai.azure.com/evaluations/cloud-run-1",
            ),
            SimpleNamespace(
                id="cloud-run-2",
                created_at="2024-05-02T10:00:00Z",
                status="succeeded",
            ),
        ],
        output_items={
            "cloud-run-1": [
                {"status": "passed", "results": {"coherence": {"score": 4.0}}},
                {"status": "passed", "results": {"coherence": {"score": 4.5}}},
            ],
            "cloud-run-2": [
                {"status": "passed", "results": {"coherence": {"score": 3.0}}},
                {"status": "failed", "results": {"coherence": {"score": 3.5}}},
            ],
        },
    )
    _install_fake_foundry_modules(monkeypatch, fake_openai)

    history = collect_results_history(
        tmp_path,
        ResultsHistorySourceConfig(enabled=True, path=".agentops/results", lookback_runs=10),
        foundry_config=FoundryControlSourceConfig(
            enabled=True,
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        ),
    )

    assert [run.run_id for run in history.runs] == ["cloud-run-1", "cloud-run-2"]
    assert history.runs[0].source == "foundry_cloud"
    assert history.runs[0].portal_url == "https://ai.azure.com/evaluations/cloud-run-1"
    assert history.runs[0].metrics["coherence"] == 4.25
    assert history.runs[1].metrics["coherence"] == 3.25
    assert history.runs[1].items_total == 2
    assert history.runs[1].items_passed_all == 1
    assert history.diagnostics["status"] == "ok"
    assert history.diagnostics["cloud"]["status"] == "ok"
    assert history.diagnostics["sources_used"] == ["foundry_cloud"]


def test_collect_results_history_keeps_local_runs_over_cloud_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    results = tmp_path / ".agentops" / "results"
    _write_run(results, "same-run", "2024-05-02T10:00:00Z", {"coherence": 5.0})
    fake_openai = _fake_openai_client(
        runs=[
            SimpleNamespace(
                id="same-run",
                created_at="2024-05-02T10:00:00Z",
                status="succeeded",
            )
        ],
        output_items={
            "same-run": [
                {"status": "passed", "results": {"coherence": {"score": 1.0}}},
            ]
        },
    )
    _install_fake_foundry_modules(monkeypatch, fake_openai)

    history = collect_results_history(
        tmp_path,
        ResultsHistorySourceConfig(enabled=True, path=".agentops/results", lookback_runs=10),
        foundry_config=FoundryControlSourceConfig(
            enabled=True,
            project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        ),
    )

    assert len(history.runs) == 1
    assert history.runs[0].source == "local"
    assert history.runs[0].metrics["coherence"] == 5.0


def _fake_openai_client(*, runs, output_items):
    eval_obj = SimpleNamespace(id="eval-1")

    class FakeOutputItems:
        @staticmethod
        def list(eval_id, run_id):
            return SimpleNamespace(data=output_items.get(run_id, []))

    class FakeRuns:
        output_items = FakeOutputItems()

        @staticmethod
        def list(eval_id, limit=10):
            return SimpleNamespace(data=runs)

    return SimpleNamespace(
        evals=SimpleNamespace(
            list=lambda limit=10: SimpleNamespace(data=[eval_obj]),
            runs=FakeRuns(),
        )
    )


def _install_fake_foundry_modules(monkeypatch, fake_openai) -> None:
    azure_module = sys.modules.get("azure") or types.ModuleType("azure")
    azure_ai_module = sys.modules.get("azure.ai") or types.ModuleType("azure.ai")
    projects_module = types.ModuleType("azure.ai.projects")
    identity_module = types.ModuleType("azure.identity")

    class FakeProjectClient:
        def __init__(self, endpoint, credential):
            self.endpoint = endpoint
            self.credential = credential

        def get_openai_client(self):
            return fake_openai

    class FakeCredential:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    projects_module.AIProjectClient = FakeProjectClient
    identity_module.DefaultAzureCredential = FakeCredential
    identity_module.AzureCliCredential = FakeCredential
    monkeypatch.setitem(sys.modules, "azure", azure_module)
    monkeypatch.setitem(sys.modules, "azure.ai", azure_ai_module)
    monkeypatch.setitem(sys.modules, "azure.ai.projects", projects_module)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)

    # The shared credential factory checks the real ``az`` CLI to decide
    # whether to prefer ``AzureCliCredential``. Reset its cache so each
    # test starts deterministic. The probe auto-skips under pytest.
    from agentops.agent.sources import _credentials

    _credentials.reset_shared_credentials()
