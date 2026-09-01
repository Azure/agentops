"""CLI-level tests for the cockpit startup Doctor-guidance echo.

The ``cockpit`` command delegates the "should we nudge the user to run
Doctor?" decision to ``cockpit_doctor_guidance`` in
``agentops.services.preflight`` and prints its result only when a line is
returned.  The pure guidance function is unit-tested in
``test_preflight.py``; these tests pin the *CLI seam* — that the command
actually echoes the guidance when the helper returns it and stays silent
when it returns ``None`` — without standing up a real uvicorn server.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import agentops.agent.cockpit as cockpit_module
import agentops.cli.app as cli_app
import agentops.services.preflight as preflight
from agentops.services.preflight import _DOCTOR_GUIDANCE


class _FakeReport:
    """Minimal stand-in for a pre-flight report with no failures."""

    has_failures = False


class _FakeServer:
    """Uvicorn ``Server`` replacement that never binds a socket."""

    def __init__(self, config: object) -> None:  # noqa: D401 - simple stub
        self.config = config
        self.started = False
        self.should_exit = False

    def run(self) -> None:
        self.started = True


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit telemetry / endpoint env from the shell."""
    monkeypatch.delenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False)
    monkeypatch.delenv(
        "AGENTOPS_APPLICATIONINSIGHTS_CONNECTION_STRING", raising=False
    )


@pytest.fixture
def _stub_cockpit_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize every side effect except the guidance echo under test."""
    uvicorn = pytest.importorskip("uvicorn")
    import webbrowser

    monkeypatch.setattr(cli_app, "_port_in_use", lambda *a, **k: False)
    monkeypatch.setattr(preflight, "run_preflight", lambda *a, **k: _FakeReport())
    monkeypatch.setattr(preflight, "format_report", lambda report: "")
    monkeypatch.setattr(cockpit_module, "create_app", lambda **k: object())
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: None)


def _invoke_cockpit(workspace: Path) -> str:
    runner = CliRunner()
    result = runner.invoke(
        cli_app.app,
        ["cockpit", "--workspace", str(workspace)],
        input="\n",
    )
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.usefixtures("_stub_cockpit_runtime")
def test_cockpit_prints_guidance_when_initialized_without_findings(
    tmp_path: Path,
) -> None:
    (tmp_path / ".agentops").mkdir()

    output = _invoke_cockpit(tmp_path)

    assert _DOCTOR_GUIDANCE in output


@pytest.mark.usefixtures("_stub_cockpit_runtime")
def test_cockpit_suppresses_guidance_before_init(tmp_path: Path) -> None:
    output = _invoke_cockpit(tmp_path)

    assert _DOCTOR_GUIDANCE not in output


@pytest.mark.usefixtures("_stub_cockpit_runtime")
def test_cockpit_suppresses_guidance_when_findings_exist(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agentops" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "history.jsonl").write_text(
        '{"run": 1}\n', encoding="utf-8"
    )

    output = _invoke_cockpit(tmp_path)

    assert _DOCTOR_GUIDANCE not in output
