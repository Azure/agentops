"""Unit tests for the GitHub Actions step-summary helpers."""

from __future__ import annotations

from agentops.core import step_summary


def test_is_active_reflects_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert step_summary.is_active() is False

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert step_summary.is_active() is True


def test_append_step_summary_noop_without_env(monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert step_summary.append_step_summary("# hello") is False


def test_append_step_summary_writes_and_appends(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert step_summary.append_step_summary("# first") is True
    assert step_summary.append_step_summary("# second\n") is True

    content = summary.read_text(encoding="utf-8")
    assert "# first" in content
    assert "# second" in content
    # Each block ends with its own newline plus a separating blank line.
    assert content.index("# first") < content.index("# second")


def test_append_report_file_appends_contents(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = tmp_path / "report.md"
    report.write_text("# AgentOps Evaluation Report\n\nPASS\n", encoding="utf-8")

    assert step_summary.append_report_file(report) is True
    content = summary.read_text(encoding="utf-8")
    assert "AgentOps Evaluation Report" in content
    assert "PASS" in content


def test_append_report_file_with_heading(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = tmp_path / "report.md"
    report.write_text("body\n", encoding="utf-8")

    assert step_summary.append_report_file(report, heading="## Eval") is True
    content = summary.read_text(encoding="utf-8")
    assert "## Eval" in content
    assert "body" in content


def test_append_report_file_missing_file_is_false(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert step_summary.append_report_file(tmp_path / "missing.md") is False
    assert summary.exists() is False or summary.read_text(encoding="utf-8") == ""


def test_append_report_file_noop_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    report = tmp_path / "report.md"
    report.write_text("body\n", encoding="utf-8")
    assert step_summary.append_report_file(report) is False
