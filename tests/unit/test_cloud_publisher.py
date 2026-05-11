"""Unit tests for the cloud (New Foundry) publisher."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from agentops.core.results import (
    RowMetric,
    RowResult,
    RunResult,
    RunSummary,
    TargetInfo,
)
from agentops.pipeline import cloud_publisher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_result(*, kind: str = "foundry_prompt", name: str = "support-bot",
                 version: str = "1") -> RunResult:
    return RunResult(
        started_at="2026-05-06T10:00:00+00:00",
        finished_at="2026-05-06T10:00:05+00:00",
        duration_seconds=5.0,
        target=TargetInfo(
            kind=kind, raw=f"{name}:{version}", name=name, version=version,
        ),
        dataset_path="dataset.jsonl",
        evaluators=["CoherenceEvaluator", "FluencyEvaluator"],
        rows=[
            RowResult(
                row_index=0,
                input="hi",
                expected="hello",
                response="hello",
                metrics=[
                    RowMetric(name="coherence", value=4.0),
                    RowMetric(name="fluency", value=4.0),
                    RowMetric(name="avg_latency_seconds", value=1.2),
                ],
            ),
        ],
        aggregate_metrics={
            "coherence": 4.0,
            "fluency": 4.0,
            "avg_latency_seconds": 1.2,
        },
        summary=RunSummary(
            items_total=1,
            items_passed_all=1,
            items_pass_rate=1.0,
            thresholds_total=0,
            thresholds_passed=0,
            threshold_pass_rate=1.0,
            overall_passed=True,
        ),
    )


@pytest.fixture
def dataset_file(tmp_path: Path) -> Path:
    """A two-row JSONL dataset on disk."""
    path = tmp_path / "dataset.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"input": "hi", "expected": "hello"}),
            json.dumps({"input": "bye", "expected": "goodbye"}),
        ]) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# _build_testing_criteria
# ---------------------------------------------------------------------------


def test_build_testing_criteria_maps_quality_evaluators(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    result = _make_result()
    criteria = cloud_publisher._build_testing_criteria(result)

    azure_names = {c["evaluator_name"] for c in criteria}
    assert "builtin.coherence" in azure_names
    assert "builtin.fluency" in azure_names
    # Every criterion is an azure_ai_evaluator entry with a stable name.
    for c in criteria:
        assert c["type"] == "azure_ai_evaluator"
        assert c["name"] in {"coherence", "fluency"}
        assert c["initialization_parameters"] == {"deployment_name": "gpt-4o-mini"}
        assert c["data_mapping"]["response"] == "{{sample.output_text}}"


def test_build_testing_criteria_skips_latency(monkeypatch):
    """avg_latency_seconds is a runtime-only metric and must NOT become an
    azure_ai_evaluator (Foundry has its own server-side latency view)."""
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    result = _make_result()
    criteria = cloud_publisher._build_testing_criteria(result)
    names = {c["name"] for c in criteria}
    assert "avg_latency_seconds" not in names


def test_build_testing_criteria_uses_selected_evaluators_when_metrics_empty(monkeypatch):
    """Cloud publish should still know what to evaluate if local invocation
    failed before any row metrics were produced."""
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    result = _make_result()
    result.aggregate_metrics.clear()

    criteria = cloud_publisher._build_testing_criteria(result)

    azure_names = {c["evaluator_name"] for c in criteria}
    assert "builtin.coherence" in azure_names
    assert "builtin.fluency" in azure_names


def test_build_testing_criteria_requires_deployment_for_ai_evaluators(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

    with pytest.raises(ValueError, match="AZURE_OPENAI_DEPLOYMENT"):
        cloud_publisher._build_testing_criteria(_make_result())


def test_build_testing_criteria_warns_on_unknown_evaluator(caplog):
    """Metrics whose preset has no azure_ai_evaluator mapping are logged
    and skipped, never raised — local results.json remains canonical."""
    result = _make_result()
    # Drop into an unknown evaluator class via monkey-patch-style override:
    # we add a synthetic preset to CATALOG so the lookup hits, but with a
    # class_name that is not in _AZURE_AI_EVALUATOR_NAMES.
    from agentops.core import evaluators as _ev

    fake = _ev.EvaluatorPreset(
        name="MyCustomEvaluator",
        class_name="MyCustomEvaluator",
        score_key="my_custom",
        input_mapping={"query": "$prompt"},
        default_threshold=None,
        categories=frozenset({"agent"}),
    )
    result.evaluators = ["MyCustomEvaluator"]
    result.aggregate_metrics.clear()
    with mock.patch.dict(_ev.CATALOG, {"MyCustomEvaluator": fake}):
        with caplog.at_level("WARNING"):
            criteria = cloud_publisher._build_testing_criteria(result)
    assert all(c["name"] != "my_custom" for c in criteria)
    assert any("no azure_ai_evaluator mapping" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _build_item_schema
# ---------------------------------------------------------------------------


def test_build_item_schema_uses_first_row_keys(dataset_file: Path):
    schema = cloud_publisher._build_item_schema(dataset_file)
    assert schema["type"] == "object"
    assert set(schema["properties"].keys()) == {"input", "expected"}
    assert set(schema["required"]) == {"input", "expected"}


def test_build_item_schema_empty_file_falls_back(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    schema = cloud_publisher._build_item_schema(empty)
    assert "input" in schema["properties"]


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


def test_publish_rejects_non_foundry_targets(dataset_file: Path):
    result = _make_result(kind="http_json")
    with pytest.raises(ValueError, match="foundry_cloud only supports"):
        cloud_publisher.publish_to_foundry_cloud(
            result,
            dataset_path=dataset_file,
            project_endpoint="https://x.example/api/projects/p",
        )


def test_publish_requires_dataset_to_exist(tmp_path: Path):
    result = _make_result()
    missing = tmp_path / "does_not_exist.jsonl"
    with pytest.raises(ValueError, match="dataset file not found"):
        cloud_publisher.publish_to_foundry_cloud(
            result,
            dataset_path=missing,
            project_endpoint="https://x.example/api/projects/p",
        )


def test_publish_requires_name_and_version(dataset_file: Path):
    """Even if target.kind is foundry_prompt, missing name/version (could
    happen with a mistakenly hand-built TargetInfo) is rejected."""
    result = _make_result()
    # Bypass model validation by reconstructing the info directly.
    result.target = TargetInfo(
        kind="foundry_prompt", raw="bot:1", name=None, version="1",
    )
    with pytest.raises(ValueError, match="fully qualified"):
        cloud_publisher.publish_to_foundry_cloud(
            result,
            dataset_path=dataset_file,
            project_endpoint="https://x.example/api/projects/p",
        )


# ---------------------------------------------------------------------------
# _poll_until_terminal
# ---------------------------------------------------------------------------


def test_poll_returns_when_status_completed():
    """Polling stops as soon as the run hits a terminal status."""
    statuses = iter(["queued", "in_progress", "completed"])

    def _retrieve(eval_id: str, run_id: str):
        return SimpleNamespace(status=next(statuses))

    fake_runs = SimpleNamespace(retrieve=_retrieve)
    fake_evals = SimpleNamespace(runs=fake_runs)
    fake_client = SimpleNamespace(evals=fake_evals)

    with mock.patch("agentops.pipeline.cloud_publisher.time.sleep") as sleep:
        run = cloud_publisher._poll_until_terminal(
            fake_client,
            eval_id="e1", run_id="r1",
            interval_seconds=0.0, max_attempts=10,
            progress=lambda _msg: None,
        )
    assert run.status == "completed"
    # We slept after each non-terminal poll (queued + in_progress).
    assert sleep.call_count == 2


def test_poll_times_out_when_never_terminal():
    """Hitting max_attempts raises a clear RuntimeError."""

    def _retrieve(eval_id: str, run_id: str):
        return SimpleNamespace(status="queued")

    fake_runs = SimpleNamespace(retrieve=_retrieve)
    fake_evals = SimpleNamespace(runs=fake_runs)
    fake_client = SimpleNamespace(evals=fake_evals)

    with mock.patch("agentops.pipeline.cloud_publisher.time.sleep"):
        with pytest.raises(RuntimeError, match="did not finish"):
            cloud_publisher._poll_until_terminal(
                fake_client,
                eval_id="e1", run_id="r1",
                interval_seconds=0.0, max_attempts=3,
                progress=lambda _msg: None,
            )


# ---------------------------------------------------------------------------
# _extract_report_url
# ---------------------------------------------------------------------------


def test_extract_report_url_from_attribute():
    run = SimpleNamespace(report_url="https://portal/x", status="completed")
    assert cloud_publisher._extract_report_url(run) == "https://portal/x"


def test_extract_report_url_from_metadata():
    run = SimpleNamespace(
        status="completed",
        metadata={"report_url": "https://portal/y"},
    )
    assert cloud_publisher._extract_report_url(run) == "https://portal/y"


def test_extract_report_url_returns_none_when_absent():
    run = SimpleNamespace(status="completed")
    assert cloud_publisher._extract_report_url(run) is None


# ---------------------------------------------------------------------------
# End-to-end happy path with a fully mocked OpenAI/Foundry client
# ---------------------------------------------------------------------------


class _FakeFiles:
    def __init__(self) -> None:
        self.uploaded: list = []

    def create(self, *, file, purpose):
        self.uploaded.append((file.name if hasattr(file, "name") else None, purpose))
        return SimpleNamespace(id="file-abc")


class _FakeRuns:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.created_with: dict = {}

    def create(self, *, eval_id, name, data_source):
        self.created_with = {
            "eval_id": eval_id, "name": name, "data_source": data_source,
        }
        return SimpleNamespace(id="run-xyz")

    def retrieve(self, *, eval_id, run_id):
        status = self._statuses.pop(0) if self._statuses else "completed"
        return SimpleNamespace(
            id=run_id,
            status=status,
            report_url="https://ai.azure.com/foundry/runs/run-xyz",
        )


class _FakeEvals:
    def __init__(self, statuses):
        self.runs = _FakeRuns(statuses)
        self.created_with: dict = {}

    def create(self, *, name, data_source_config, testing_criteria):
        self.created_with = {
            "name": name,
            "data_source_config": data_source_config,
            "testing_criteria": testing_criteria,
        }
        return SimpleNamespace(id="eval-123")


class _FakeOpenAIClient:
    def __init__(self, statuses):
        self.files = _FakeFiles()
        self.evals = _FakeEvals(statuses)


class _FakeProjectClient:
    def __init__(self, openai_client):
        self._openai = openai_client

    def get_openai_client(self):
        # NB: must be callable with NO arguments — we never want callers
        # to pass api_version (regression guard).
        return self._openai


def test_publish_to_foundry_cloud_happy_path(dataset_file: Path):
    """End-to-end happy path with all Azure SDKs mocked.

    Verifies:
    - dataset is uploaded with purpose='evals'
    - testing_criteria contain only mappable evaluators (coherence + fluency)
    - data_source carries an azure_ai_agent target with name + version
    - target is built from result.target (not the raw string)
    - polling runs to completion and the result captures the portal URL
    """
    fake_openai = _FakeOpenAIClient(statuses=["queued", "completed"])
    fake_project = _FakeProjectClient(fake_openai)

    fake_projects_module = mock.MagicMock()
    fake_projects_module.AIProjectClient = mock.MagicMock(return_value=fake_project)
    fake_identity_module = mock.MagicMock()

    progress_messages: list = []

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_module,
            "azure.identity": fake_identity_module,
        },
    ):
        with mock.patch.dict(
            "os.environ", {"AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini"}
        ):
            with mock.patch("agentops.pipeline.cloud_publisher.time.sleep"):
                published = cloud_publisher.publish_to_foundry_cloud(
                    _make_result(),
                dataset_path=dataset_file,
                project_endpoint="https://contoso.services.ai.azure.com/api/projects/p",
                poll_interval_seconds=0.0,
                max_poll_attempts=5,
                progress=progress_messages.append,
            )


    # The SDK was called with the right project endpoint.
    fake_projects_module.AIProjectClient.assert_called_once()
    _, kwargs = fake_projects_module.AIProjectClient.call_args
    assert kwargs["endpoint"].endswith("/api/projects/p")

    # The dataset was uploaded for evals.
    assert fake_openai.files.uploaded
    assert fake_openai.files.uploaded[0][1] == "evals"

    # Testing criteria contain only the mappable evaluators.
    criteria = fake_openai.evals.created_with["testing_criteria"]
    azure_names = {c["evaluator_name"] for c in criteria}
    assert "builtin.coherence" in azure_names
    assert "builtin.fluency" in azure_names
    assert all(c["initialization_parameters"]["deployment_name"] == "gpt-4o-mini" for c in criteria)
    assert all("data_mapping" in c for c in criteria)

    # The data_source uses azure_ai_target_completions with the right agent.
    data_source = fake_openai.evals.runs.created_with["data_source"]
    assert data_source["type"] == "azure_ai_target_completions"
    target = data_source["target"]
    assert target["type"] == "azure_ai_agent"
    assert target["name"] == "support-bot"
    assert target["version"] == "1"
    assert data_source["input_messages"]["template"][0]["content"]["text"] == "{{item.input}}"
    assert data_source["source"] == {"type": "file_id", "id": "file-abc"}

    # Result captures status + portal URL.
    assert published.status == "completed"
    assert published.eval_id == "eval-123"
    assert published.run_id == "run-xyz"
    assert published.report_url == "https://ai.azure.com/foundry/runs/run-xyz"

    # Progress messages went through.
    assert any("uploading" in m for m in progress_messages)
    assert any("status -> completed" in m for m in progress_messages)


def test_publish_to_foundry_cloud_raises_when_run_fails(dataset_file: Path):
    """A non-completed terminal status surfaces as a RuntimeError so the
    orchestrator can downgrade it to a warning + best-effort log."""
    fake_openai = _FakeOpenAIClient(statuses=["failed"])
    fake_project = _FakeProjectClient(fake_openai)

    fake_projects_module = mock.MagicMock()
    fake_projects_module.AIProjectClient = mock.MagicMock(return_value=fake_project)
    fake_identity_module = mock.MagicMock()

    with mock.patch.dict(
        "sys.modules",
        {
            "azure.ai.projects": fake_projects_module,
            "azure.identity": fake_identity_module,
        },
    ):
        with mock.patch.dict(
            "os.environ", {"AZURE_OPENAI_DEPLOYMENT": "gpt-4o-mini"}
        ):
            with mock.patch("agentops.pipeline.cloud_publisher.time.sleep"):
                with pytest.raises(RuntimeError, match="status 'failed'"):
                    cloud_publisher.publish_to_foundry_cloud(
                        _make_result(),
                        dataset_path=dataset_file,
                        project_endpoint="https://x.example/api/projects/p",
                        poll_interval_seconds=0.0,
                        max_poll_attempts=2,
                    )
