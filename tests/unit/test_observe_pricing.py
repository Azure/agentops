from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agentops.agent.knowledge.pricing import load_packaged_price_reference
import agentops.agent.knowledge.pricing as pricing_loader
from agentops.core.observe_pricing import load_price_reference
from fixtures.observe import make_price_entry, make_price_reference_payload


def _raw(**overrides: object) -> str:
    return json.dumps(make_price_reference_payload(version="fixture-v1", **overrides))


def test_price_reference_preserves_decimal_model_token_class_and_provenance() -> None:
    result = load_price_reference(
        _raw(
            entries=[
                make_price_entry(token_class="input", unit_price="0.0000001"),
                make_price_entry(token_class="output", unit_price="0.0000009"),
                make_price_entry(token_class="cache_read", unit_price="0.00000001"),
            ]
        )
    )

    assert result.state == "valid"
    assert result.reference is not None
    assert result.reference.version == "fixture-v1"
    assert result.reference.effective_date == date(2026, 8, 1)
    prices = result.reference.prices_by_model()["gpt-5-nano"]
    assert prices["input"].unit_price == Decimal("0.0000001")
    assert prices["output"].unit_price == Decimal("0.0000009")
    assert prices["cache_read"].unit_price == Decimal("0.00000001")
    assert all(not isinstance(entry.unit_price, float) for entry in result.reference.entries)


def test_price_reference_staleness_is_strictly_more_than_ninety_days() -> None:
    result = load_price_reference(_raw(effective_date="2026-05-31"))
    assert result.reference is not None
    assert result.reference.age_days(date(2026, 8, 29)) == 90
    assert result.reference.is_stale(date(2026, 8, 29)) is False
    assert result.reference.age_days(date(2026, 8, 30)) == 91
    assert result.reference.is_stale(date(2026, 8, 30)) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "v1", "effective_date": "bad", "source": "source", "entries": []},
        make_price_reference_payload(
            version="v1",
            entries=[
                make_price_entry(),
                make_price_entry(unit_price="0.06"),
            ],
        ),
        make_price_reference_payload(
            version="v1", entries=[make_price_entry(unit_price=0.05)]
        ),
        make_price_reference_payload(
            version="v1", entries=[make_price_entry(unit_price="0")]
        ),
    ],
)
def test_price_reference_rejects_invalid_or_duplicate_entries(
    payload: dict[str, object],
) -> None:
    result = load_price_reference(json.dumps(payload))
    assert result.state == "invalid"
    assert result.reference is None
    assert result.message


def test_price_reference_rejects_boolean_per_tokens() -> None:
    payload = make_price_reference_payload(
        entries=[make_price_entry(per_tokens=True)]
    )

    result = load_price_reference(json.dumps(payload))

    assert result.state == "invalid"
    assert result.reference is None


def test_packaged_reference_loads_without_credentials_or_network() -> None:
    load_packaged_price_reference.cache_clear()
    result = load_packaged_price_reference()
    assert result.state == "valid"
    assert result.reference is not None
    assert result.reference.source
    assert {"input", "output", "cache_read"} <= {
        entry.token_class for entry in result.reference.entries
    }


@pytest.mark.parametrize("failure", [FileNotFoundError(), OSError("unreadable")])
def test_packaged_reference_loader_degrades_when_file_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, failure: OSError
) -> None:
    class MissingReference:
        def joinpath(self, _name: str) -> "MissingReference":
            return self

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            raise failure

    monkeypatch.setattr(pricing_loader.resources, "files", lambda _package: MissingReference())
    load_packaged_price_reference.cache_clear()
    result = load_packaged_price_reference()

    assert result.state in {"absent", "invalid"}
    assert result.reference is None
    assert result.message
    load_packaged_price_reference.cache_clear()


def test_packaged_reference_loader_degrades_when_file_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidReference:
        def joinpath(self, _name: str) -> "InvalidReference":
            return self

        def read_text(self, *, encoding: str) -> str:
            assert encoding == "utf-8"
            return "{not valid json"

    monkeypatch.setattr(pricing_loader.resources, "files", lambda _package: InvalidReference())
    load_packaged_price_reference.cache_clear()
    result = load_packaged_price_reference()
    assert result.state == "invalid"
    assert result.message
    load_packaged_price_reference.cache_clear()


def test_built_wheel_contains_packaged_price_reference(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(root),
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
            "--disable-pip-version-check",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    wheel = next(tmp_path.glob("agentops_accelerator-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert (
            "agentops/agent/observe/pricing/list-prices.json" in archive.namelist()
        )
