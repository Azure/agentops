"""Tests for pre-flight checks."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from agentops.core.models import (
    BundleConfig,
    BundleRef,
    DatasetRef,
    LocalAdapterConfig,
    RunConfig,
    TargetConfig,
    TargetEndpointConfig,
)
from agentops.services.preflight import (
    PreflightReport,
    _needs_ai_assisted_evaluator,
    _needs_azure_sdk,
    _needs_safety_evaluator,
    run_preflight_checks,
)


def _local_run_config() -> RunConfig:
    return RunConfig(
        version=1,
        target=TargetConfig(
            type="model",
            hosting="local",
            execution_mode="local",
            local=LocalAdapterConfig(adapter="python fake.py"),
        ),
        bundle=BundleRef(name="fake"),
        dataset=DatasetRef(name="fake"),
    )


def _remote_http_run_config(url: str = "https://example.invalid/agent") -> RunConfig:
    return RunConfig(
        version=1,
        target=TargetConfig(
            type="agent",
            hosting="containerapps",
            execution_mode="remote",
            endpoint=TargetEndpointConfig(kind="http", url=url),
        ),
        bundle=BundleRef(name="fake"),
        dataset=DatasetRef(name="fake"),
    )


def _local_bundle() -> BundleConfig:
    return BundleConfig(
        version=1,
        name="local_only",
        evaluators=[
            {"name": "exact_match", "source": "local", "enabled": True},
        ],
    )


def _ai_assisted_bundle() -> BundleConfig:
    return BundleConfig(
        version=1,
        name="rag_quality",
        evaluators=[
            {"name": "RelevanceEvaluator", "source": "foundry", "enabled": True},
        ],
    )


def _safety_bundle() -> BundleConfig:
    return BundleConfig(
        version=1,
        name="safety",
        evaluators=[
            {"name": "ViolenceEvaluator", "source": "foundry", "enabled": True},
        ],
    )


def test_preflight_report_ok_when_empty() -> None:
    report = PreflightReport()
    assert report.ok is True


def test_preflight_report_format_lists_errors() -> None:
    report = PreflightReport(errors=["error 1", "error 2"])
    text = report.format()
    assert "Pre-flight checks failed" in text
    assert "1. error 1" in text
    assert "2. error 2" in text


def test_needs_azure_sdk_false_for_local_only() -> None:
    assert _needs_azure_sdk(_local_bundle()) is False


def test_needs_azure_sdk_true_for_foundry() -> None:
    assert _needs_azure_sdk(_ai_assisted_bundle()) is True


def test_needs_ai_assisted_true_for_ai_class() -> None:
    assert _needs_ai_assisted_evaluator(_ai_assisted_bundle()) is True


def test_needs_ai_assisted_false_for_safety() -> None:
    assert _needs_ai_assisted_evaluator(_safety_bundle()) is False


def test_needs_safety_true_for_safety_class() -> None:
    assert _needs_safety_evaluator(_safety_bundle()) is True


def test_preflight_local_only_skips_azure_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    # No Azure env vars set, local-only bundle -> no errors.
    for var in (
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    ):
        monkeypatch.delenv(var, raising=False)

    report = run_preflight_checks(_local_run_config(), _local_bundle())
    assert report.ok is True


def test_preflight_missing_env_vars_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    with mock.patch(
        "agentops.services.preflight.importlib.import_module"
    ) as imp, mock.patch("agentops.services.preflight._check_credentials"):
        imp.return_value = mock.Mock()
        report = run_preflight_checks(_local_run_config(), _ai_assisted_bundle())

    assert report.ok is False
    combined = " ".join(report.errors)
    assert "AZURE_OPENAI_ENDPOINT" in combined
    assert "AZURE_OPENAI_DEPLOYMENT" in combined


def test_preflight_missing_foundry_project_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)

    with mock.patch(
        "agentops.services.preflight.importlib.import_module"
    ) as imp, mock.patch("agentops.services.preflight._check_credentials"):
        imp.return_value = mock.Mock()
        report = run_preflight_checks(_local_run_config(), _safety_bundle())

    assert report.ok is False
    assert any(
        "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT" in e for e in report.errors
    )


def test_preflight_missing_sdk_reports_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib as _importlib

    real_import = _importlib.import_module

    def fake_import(name: str) -> object:
        if name in ("azure.identity", "azure.ai.evaluation"):
            raise ImportError("no module")
        return real_import(name)

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    with mock.patch(
        "agentops.services.preflight.importlib.import_module", side_effect=fake_import
    ), mock.patch("agentops.services.preflight._check_credentials"):
        report = run_preflight_checks(_local_run_config(), _ai_assisted_bundle())

    assert report.ok is False
    combined = " ".join(report.errors)
    assert "azure-identity" in combined
    assert "azure-ai-evaluation" in combined
    assert "pip install" in combined


def test_preflight_endpoint_unreachable_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    from urllib import error as urllib_error

    with mock.patch(
        "agentops.services.preflight.importlib.import_module"
    ) as imp, mock.patch(
        "agentops.services.preflight._check_credentials"
    ), mock.patch(
        "agentops.services.preflight.urllib_request.urlopen",
        side_effect=urllib_error.URLError("Name or service not known"),
    ):
        imp.return_value = mock.Mock()
        report = run_preflight_checks(
            _remote_http_run_config(), _ai_assisted_bundle()
        )

    assert report.ok is False
    assert any("unreachable" in e.lower() for e in report.errors)


def test_preflight_collects_multiple_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight should surface all detectable issues at once."""
    import importlib as _importlib

    real_import = _importlib.import_module

    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)

    def fake_import(name: str) -> object:
        if name in ("azure.identity", "azure.ai.evaluation"):
            raise ImportError("no module")
        return real_import(name)

    with mock.patch(
        "agentops.services.preflight.importlib.import_module", side_effect=fake_import
    ):
        report = run_preflight_checks(_local_run_config(), _ai_assisted_bundle())

    # Should report both missing SDK and missing env vars.
    assert len(report.errors) >= 2


def test_preflight_http_endpoint_ok_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib as _importlib

    real_import = _importlib.import_module

    def fake_import(name: str) -> object:
        if name in ("azure.identity", "azure.ai.evaluation"):
            return mock.MagicMock()
        return real_import(name)

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")

    with mock.patch(
        "agentops.services.preflight.importlib.import_module", side_effect=fake_import
    ), mock.patch(
        "agentops.services.preflight._check_credentials"
    ), mock.patch(
        "agentops.services.preflight.urllib_request.urlopen"
    ) as urlopen:
        urlopen.return_value = mock.MagicMock()
        report = run_preflight_checks(
            _remote_http_run_config(), _ai_assisted_bundle()
        )

    assert report.ok is True
