"""Unit tests for the HTTP backend."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from agentops.backends.base import BackendRunContext
from agentops.backends.http_backend import HttpBackend, _extract_dot_path
from agentops.core.models import BackendConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUNDLE_YAML = """\
version: 1
name: test_http_bundle
evaluators:
  - name: exact_match
    source: local
    enabled: true
  - name: avg_latency_seconds
    source: local
    enabled: true
thresholds:
  - evaluator: exact_match
    criteria: ">="
    value: 0.5
"""

_DATASET_YAML = """\
version: 1
name: test_http_dataset
source:
  type: file
  path: smoke.jsonl
format:
  type: jsonl
  input_field: input
  expected_field: expected
"""

_DATASET_ROWS = [
    {"id": "1", "input": "What is 2+2?", "expected": "4"},
    {"id": "2", "input": "Capital of France?", "expected": "Paris"},
]


def _write_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    bundle_path = tmp_path / "bundle.yaml"
    dataset_path = tmp_path / "dataset.yaml"
    data_path = tmp_path / "smoke.jsonl"

    bundle_path.write_text(_BUNDLE_YAML, encoding="utf-8")
    dataset_path.write_text(_DATASET_YAML, encoding="utf-8")
    data_path.write_text(
        "\n".join(json.dumps(row) for row in _DATASET_ROWS), encoding="utf-8"
    )
    return bundle_path, dataset_path


def _build_context(
    tmp_path: Path,
    *,
    url: str = "http://localhost:8080/chat",
    request_field: str = "message",
    response_field: str = "text",
    auth_header_env: str | None = None,
    headers: Dict[str, str] | None = None,
) -> BackendRunContext:
    bundle_path, dataset_path = _write_fixtures(tmp_path)
    backend_config = BackendConfig(
        type="http",
        url=url,
        request_field=request_field,
        response_field=response_field,
        auth_header_env=auth_header_env,
        headers=headers or {},
        timeout_seconds=30,
    )
    return BackendRunContext(
        backend_config=backend_config,
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out",
    )


def _fake_urlopen(response_body: Dict[str, Any]):
    """Return a context-manager mock that yields a fake HTTP response."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(response_body).encode("utf-8")
    mock_response.__enter__ = lambda self: self
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# ---------------------------------------------------------------------------
# _extract_dot_path
# ---------------------------------------------------------------------------


def test_extract_dot_path_single_key() -> None:
    assert _extract_dot_path({"text": "hello"}, "text") == "hello"


def test_extract_dot_path_nested() -> None:
    assert _extract_dot_path({"output": {"text": "world"}}, "output.text") == "world"


def test_extract_dot_path_missing_key_raises() -> None:
    with pytest.raises(ValueError, match="Response field 'missing'"):
        _extract_dot_path({"text": "hi"}, "missing")


def test_extract_dot_path_non_dict_intermediate_raises() -> None:
    with pytest.raises(ValueError, match="expected object at 'nested'"):
        _extract_dot_path({"text": "flat"}, "text.nested")


# ---------------------------------------------------------------------------
# BackendConfig validation
# ---------------------------------------------------------------------------


def test_backend_config_accepts_http_with_url() -> None:
    config = BackendConfig.model_validate({"type": "http", "url": "http://localhost/chat"})
    assert config.type == "http"
    assert config.url == "http://localhost/chat"


def test_backend_config_accepts_http_with_url_env() -> None:
    config = BackendConfig.model_validate({"type": "http", "url_env": "AGENT_HTTP_URL"})
    assert config.type == "http"
    assert config.url_env == "AGENT_HTTP_URL"


def test_backend_config_http_requires_url_or_url_env() -> None:
    with pytest.raises(Exception, match="url"):
        BackendConfig.model_validate({"type": "http"})


# ---------------------------------------------------------------------------
# HttpBackend URL resolution
# ---------------------------------------------------------------------------


def test_resolve_url_from_config(tmp_path: Path) -> None:
    context = _build_context(tmp_path, url="http://example.com/api")
    backend = HttpBackend()
    assert backend._resolve_url(context) == "http://example.com/api"


def test_resolve_url_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_AGENT_URL", "http://agent.example.com/chat")
    bundle_path, dataset_path = _write_fixtures(tmp_path)
    config = BackendConfig(type="http", url_env="MY_AGENT_URL")
    context = BackendRunContext(
        backend_config=config,
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out",
    )
    backend = HttpBackend()
    assert backend._resolve_url(context) == "http://agent.example.com/chat"


def test_resolve_url_env_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_URL_VAR", raising=False)
    bundle_path, dataset_path = _write_fixtures(tmp_path)
    config = BackendConfig(type="http", url_env="MISSING_URL_VAR")
    context = BackendRunContext(
        backend_config=config,
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out",
    )
    backend = HttpBackend()
    with pytest.raises(ValueError, match="MISSING_URL_VAR"):
        backend._resolve_url(context)


# ---------------------------------------------------------------------------
# HttpBackend.execute — happy path
# ---------------------------------------------------------------------------


def test_execute_posts_to_url_and_writes_metrics(tmp_path: Path) -> None:
    context = _build_context(tmp_path, request_field="message", response_field="text")
    fake_response = {"text": "4"}

    with patch("agentops.backends.http_backend.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_urlopen(fake_response)
        result = HttpBackend().execute(context)

    metrics_path = context.backend_output_dir / "backend_metrics.json"
    assert metrics_path.exists()
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert "metrics" in payload
    assert "row_metrics" in payload
    assert len(payload["row_metrics"]) == len(_DATASET_ROWS)


def test_execute_uses_correct_request_field(tmp_path: Path) -> None:
    context = _build_context(tmp_path, request_field="query", response_field="answer")
    calls: list[dict] = []

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        calls.append(body)
        mock = _fake_urlopen({"answer": "some answer"})
        return mock

    with patch("agentops.backends.http_backend.urllib.request.urlopen", side_effect=fake_urlopen):
        HttpBackend().execute(context)

    assert len(calls) == len(_DATASET_ROWS)
    for call, row in zip(calls, _DATASET_ROWS):
        assert "query" in call
        assert call["query"] == row["input"]
        assert "message" not in call


def test_execute_dot_path_response_extraction(tmp_path: Path) -> None:
    context = _build_context(tmp_path, response_field="output.text")
    fake_response = {"output": {"text": "Paris"}}

    with patch("agentops.backends.http_backend.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_urlopen(fake_response)
        result = HttpBackend().execute(context)

    assert result.exit_code == 0
    payload = json.loads(
        (context.backend_output_dir / "backend_metrics.json").read_text(encoding="utf-8")
    )
    assert len(payload["row_metrics"]) == len(_DATASET_ROWS)


def test_execute_exact_match_scores(tmp_path: Path) -> None:
    """Row 1: matches (2+2=4 → '4'), row 2: does not match ('Paris' vs 'Paris' — same)."""
    responses = [{"text": "4"}, {"text": "Paris"}]
    call_index = 0

    def fake_urlopen(request, timeout=None):
        nonlocal call_index
        mock = _fake_urlopen(responses[call_index % len(responses)])
        call_index += 1
        return mock

    context = _build_context(tmp_path)
    with patch("agentops.backends.http_backend.urllib.request.urlopen", side_effect=fake_urlopen):
        HttpBackend().execute(context)

    payload = json.loads(
        (context.backend_output_dir / "backend_metrics.json").read_text(encoding="utf-8")
    )
    row_metrics = payload["row_metrics"]
    assert len(row_metrics) == 2

    for rm in row_metrics:
        names = {m["name"] for m in rm["metrics"]}
        assert "exact_match" in names
        assert "avg_latency_seconds" in names


def test_execute_sets_auth_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret-token-123")
    context = _build_context(tmp_path, auth_header_env="MY_TOKEN")
    captured_headers: list[dict] = []

    def fake_urlopen(request, timeout=None):
        captured_headers.append(dict(request.headers))
        return _fake_urlopen({"text": "4"})

    with patch("agentops.backends.http_backend.urllib.request.urlopen", side_effect=fake_urlopen):
        HttpBackend().execute(context)

    for headers in captured_headers:
        # urllib capitalizes the first letter of each header word
        auth = headers.get("Authorization") or headers.get("authorization")
        assert auth == "Bearer secret-token-123"


def test_execute_forwards_extra_row_fields(tmp_path: Path) -> None:
    """Extra JSONL fields (session_id, user_id) must appear in the request body;
    input_field and expected_field must be stripped."""
    bundle_path = tmp_path / "bundle.yaml"
    dataset_path = tmp_path / "dataset.yaml"
    data_path = tmp_path / "smoke.jsonl"
    bundle_path.write_text(_BUNDLE_YAML, encoding="utf-8")
    dataset_path.write_text(_DATASET_YAML, encoding="utf-8")
    data_path.write_text(
        json.dumps({"id": "1", "input": "Q?", "expected": "A", "session_id": "s42", "user_id": "u7"}),
        encoding="utf-8",
    )
    config = BackendConfig(type="http", url="http://localhost/chat", timeout_seconds=5)
    context = BackendRunContext(
        backend_config=config,
        bundle_path=bundle_path,
        dataset_path=dataset_path,
        backend_output_dir=tmp_path / "out",
    )
    calls: list[dict] = []

    def fake_urlopen(request, timeout=None):
        calls.append(json.loads(request.data.decode("utf-8")))
        return _fake_urlopen({"text": "A"})

    with patch("agentops.backends.http_backend.urllib.request.urlopen", side_effect=fake_urlopen):
        HttpBackend().execute(context)

    assert len(calls) == 1
    body = calls[0]
    assert body["message"] == "Q?"       # prompt under request_field
    assert body["session_id"] == "s42"   # forwarded
    assert body["user_id"] == "u7"       # forwarded
    assert body["id"] == "1"             # forwarded
    assert "input" not in body           # stripped (input_field)
    assert "expected" not in body        # stripped (expected_field)


def test_execute_includes_extra_headers(tmp_path: Path) -> None:
    context = _build_context(tmp_path, headers={"X-Custom-Header": "myvalue"})
    captured_headers: list[dict] = []

    def fake_urlopen(request, timeout=None):
        captured_headers.append(dict(request.headers))
        return _fake_urlopen({"text": "4"})

    with patch("agentops.backends.http_backend.urllib.request.urlopen", side_effect=fake_urlopen):
        HttpBackend().execute(context)

    for headers in captured_headers:
        custom = headers.get("X-custom-header") or headers.get("X-Custom-Header")
        assert custom == "myvalue"


# ---------------------------------------------------------------------------
# HttpBackend.execute — error handling
# ---------------------------------------------------------------------------


def test_execute_returns_nonzero_exit_code_on_http_error(tmp_path: Path) -> None:
    import urllib.error

    context = _build_context(tmp_path)

    with patch(
        "agentops.backends.http_backend.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = HttpBackend().execute(context)

    assert result.exit_code == 1
    stderr = (context.backend_output_dir / "backend.stderr.log").read_text(encoding="utf-8")
    assert "connection refused" in stderr.lower() or "row=1" in stderr


def test_execute_writes_stdout_log(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    with patch("agentops.backends.http_backend.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_urlopen({"text": "4"})
        HttpBackend().execute(context)

    stdout = (context.backend_output_dir / "backend.stdout.log").read_text(encoding="utf-8")
    assert "row=1" in stdout


def test_execute_result_backend_label(tmp_path: Path) -> None:
    context = _build_context(tmp_path)
    with patch("agentops.backends.http_backend.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _fake_urlopen({"text": "4"})
        result = HttpBackend().execute(context)

    assert result.backend == "http"
    assert result.started_at.endswith("Z")
    assert result.finished_at.endswith("Z")
    assert result.duration_seconds >= 0.0
