"""Tests for :mod:`agentops.utils.azure_endpoints`."""

from __future__ import annotations

import pytest

from agentops.utils.azure_endpoints import normalize_azure_openai_endpoint


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Already-clean values are returned untouched (apart from trailing slash).
        ("https://x.openai.azure.com", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/", "https://x.openai.azure.com"),
        # The portal-style suffix is stripped.
        ("https://x.openai.azure.com/openai/v1", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/v1/", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/v1//", "https://x.openai.azure.com"),
        # Other well-known variants.
        ("https://x.openai.azure.com/openai", "https://x.openai.azure.com"),
        ("https://x.openai.azure.com/openai/", "https://x.openai.azure.com"),
        (
            "https://x.openai.azure.com/openai/deployments",
            "https://x.openai.azure.com",
        ),
        # Case-insensitive suffix match.
        ("https://x.openai.azure.com/OpenAI/V1", "https://x.openai.azure.com"),
        # The AI Services proxy host is also accepted.
        (
            "https://aif-prj.cognitiveservices.azure.com/openai/v1",
            "https://aif-prj.cognitiveservices.azure.com",
        ),
        # Whitespace is trimmed.
        ("  https://x.openai.azure.com/openai/v1  ", "https://x.openai.azure.com"),
    ],
)
def test_normalize_strips_known_suffixes(raw: str, expected: str) -> None:
    assert normalize_azure_openai_endpoint(raw) == expected


def test_normalize_passes_through_none() -> None:
    assert normalize_azure_openai_endpoint(None) is None


def test_normalize_passes_through_empty_string() -> None:
    assert normalize_azure_openai_endpoint("") == ""
    assert normalize_azure_openai_endpoint("   ") == ""


def test_normalize_only_strips_at_the_end() -> None:
    # A path containing "openai" earlier in the URL must not be touched.
    raw = "https://proxy.example.com/openai/v1/extra"
    assert normalize_azure_openai_endpoint(raw) == raw.rstrip("/")
