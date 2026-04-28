"""Tests for the watchdog agent findings model."""

from agentops.agent.findings import Finding, Severity, severity_emoji


def test_severity_ordering() -> None:
    assert Severity.INFO < Severity.WARNING < Severity.CRITICAL
    assert Severity.CRITICAL > Severity.WARNING
    assert Severity.WARNING <= Severity.WARNING
    assert Severity.CRITICAL >= Severity.INFO


def test_finding_to_dict_roundtrip() -> None:
    finding = Finding(
        id="x.y",
        severity=Severity.WARNING,
        title="t",
        summary="s",
        recommendation="r",
        source="results_history",
        evidence={"k": 1},
    )
    payload = finding.to_dict()
    assert payload["severity"] == "warning"
    assert payload["evidence"] == {"k": 1}


def test_severity_emoji_mapping() -> None:
    for sev in Severity:
        assert severity_emoji(sev)
