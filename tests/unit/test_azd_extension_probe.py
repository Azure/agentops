"""Tests for azd extension availability detection.

Covers quickstart scenario T1.4. The important case is the false-positive
guard: the human-readable ``azd extension list`` table includes rows for
extensions that are in the registry but NOT installed, so a substring scan
reports availability for something the user cannot actually use.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentops.pipeline import azd_runner  # noqa: E402
from fixtures.azd_stub import (  # noqa: E402
    AzdStub,
    extension_list_json,
    extension_list_table,
)


CURRENT = "azure.ai.evaluations"
LEGACY = "azure.ai.agents"


def _stub_with_version(monkeypatch) -> AzdStub:
    stub = AzdStub()
    stub.expect("azd", "version")
    stub.install(monkeypatch)
    return stub


def test_structured_listing_containing_the_extension_means_available(monkeypatch) -> None:
    stub = _stub_with_version(monkeypatch)
    stub.expect(
        "extension", "list", "--installed", "-o", "json",
        stdout=extension_list_json(LEGACY, CURRENT),
    )

    assert azd_runner.probe_extension(CURRENT) is True
    stub.assert_exhausted()


def test_structured_listing_omitting_the_extension_means_unavailable(monkeypatch) -> None:
    stub = _stub_with_version(monkeypatch)
    stub.expect(
        "extension", "list", "--installed", "-o", "json",
        stdout=extension_list_json(LEGACY),
    )

    assert azd_runner.probe_extension(CURRENT) is False
    stub.assert_exhausted()


def test_structured_listing_is_authoritative_and_skips_the_text_fallback(monkeypatch) -> None:
    """An empty installed list must not fall through to a text scan."""

    stub = _stub_with_version(monkeypatch)
    stub.expect("extension", "list", "--installed", "-o", "json", stdout="[]")

    assert azd_runner.probe_extension(CURRENT) is False
    stub.assert_exhausted()
    # Exactly one extension listing, and it is the structured one. A text
    # fallback call here would mean the empty structured answer was ignored.
    listings = stub.find("extension", "list")
    assert len(listings) == 1
    assert "--installed" in listings[0]


def test_not_installed_row_in_the_text_table_is_not_availability(monkeypatch) -> None:
    """The false-positive guard.

    The table lists every registry extension. A ``Not installed`` row still
    contains the extension id, so a substring scan would wrongly report the
    extension as usable.
    """

    stub = _stub_with_version(monkeypatch)
    stub.expect(
        "extension", "list", "--installed", "-o", "json",
        stdout=extension_list_json(LEGACY),
    )

    assert azd_runner.probe_extension(CURRENT) is False


def test_text_fallback_is_used_when_structured_output_is_unsupported(monkeypatch) -> None:
    """Older azd builds without the structured form keep the previous behavior."""

    stub = _stub_with_version(monkeypatch)
    stub.expect(
        "extension", "list", "--installed", "-o", "json",
        returncode=2,
        stderr="unknown flag: --installed",
    )
    stub.expect(
        "extension", "list",
        stdout=extension_list_table((LEGACY, "Up to date")),
    )

    assert azd_runner.probe_extension(LEGACY) is True
    stub.assert_exhausted()


def test_unparseable_structured_output_falls_back_rather_than_denying(monkeypatch) -> None:
    stub = _stub_with_version(monkeypatch)
    stub.expect("extension", "list", "--installed", "-o", "json", stdout="not json at all")
    stub.expect(
        "extension", "list",
        stdout=extension_list_table((LEGACY, "Up to date")),
    )

    assert azd_runner.probe_extension(LEGACY) is True
    stub.assert_exhausted()


def test_missing_azd_binary_means_unavailable(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise FileNotFoundError("azd")

    monkeypatch.setattr(subprocess, "run", _raise)

    assert azd_runner.probe_extension(CURRENT) is False


def test_azd_available_delegates_to_the_shared_probe(monkeypatch) -> None:
    """The legacy entry point keeps its signature and answers for the legacy id."""

    seen: list[str] = []

    def _probe(extension_name: str, *, cwd=None) -> bool:
        seen.append(extension_name)
        return True

    monkeypatch.setattr(azd_runner, "probe_extension", _probe)

    assert azd_runner.azd_available() is True
    assert seen == [LEGACY]
